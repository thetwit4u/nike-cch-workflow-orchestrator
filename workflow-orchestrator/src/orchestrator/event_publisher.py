import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from clients.sns_client import SnsClient
from orchestrator.context_merge import merge_branch_context
from orchestrator.state import WorkflowState

logger = logging.getLogger(__name__)

class EventPublisher:
    """
    A class to handle the construction and publishing of CchSystemEvent messages.
    """
    def __init__(self):
        self.sns_client = SnsClient()
        self.topic_arn = os.getenv("SYSTEM_EVENTS_TOPIC_ARN")

    def publish_event(self, state: dict, current_step: Dict[str, Any], next_steps: list[Dict[str, Any]], status: str) -> None:
        """
        Constructs and publishes a CchSystemEvent for a completed workflow step.
        """
        self._publish(state, current_step, next_steps, status)

    def publish_start_event(self, initial_state: dict, next_steps: list[Dict[str, Any]]) -> None:
        """
        Constructs and publishes a CchSystemEvent for the start of a workflow.
        """
        current_step = {"name": "WorkflowStart", "title": "Workflow Started", "type": "start"}
        self._publish(initial_state, current_step, next_steps, "Started")

    def _publish(self, state: dict, current_step: Optional[Dict[str, Any]], next_steps: List[Dict[str, Any]], status: str) -> None:
        if not self.topic_arn:
            logger.warning("SYSTEM_EVENTS_TOPIC_ARN environment variable not set. Skipping event publication.")
            return

        context = state.get("context") if isinstance(state, dict) else None
        if not isinstance(context, dict):
            logger.warning(f"Context is not a valid dictionary in state. Cannot publish event.")
            return

        data = state.get("data", {})
        message_group_id = data.get("consignmentId")
        if not message_group_id:
            logger.warning(f"consignmentId is missing from data. Cannot publish event.")
            return

        event = self._build_event(state, current_step, next_steps, status)

        try:
            self.sns_client.publish_message(
                topic_arn=self.topic_arn,
                message_body=event,
                message_group_id=message_group_id
            )
        except Exception as e:
            logger.error(f"Failed to publish event: {e}", exc_info=True)

    def _build_event(self, state: dict, current_step: Optional[Dict[str, Any]], next_steps: List[Dict[str, Any]], status: str) -> Dict[str, Any]:
        """
        Builds the CchSystemEvent object.
        """
        context = state.get("context", {})
        event_type = "WorkflowStateUpdated"
        if status == "Started":
            event_type = "WorkflowStateInitiated"
        elif status == "Ended":
            event_type = "WorkflowStateEnded"
        elif status == "Started:AsyncRequest":
            event_type = "AsyncRequestStarted"
        elif status == "Ended:AsyncRequest":
            event_type = "AsyncRequestEnded"
        elif status == "Started:EventWait":
            event_type = "EventWaitStarted"
        elif status == "Ended:EventWait":
            event_type = "EventWaitEnded"
        elif status == "Started:MapFork":
            event_type = "MapForkStarted"
        elif status == "Ended:Branch":
            event_type = "BranchEnded"

        raw_data = state.get("data", {}) or {}
        # Determine the most accurate branch item to fold into business context
        context_obj = state.get("context", {}) or {}
        branch_key = context_obj.get("branch_key")
        map_items_by_key = (context_obj.get("map_items_by_key") or {}) if isinstance(context_obj, dict) else {}
        branch_item_by_key = map_items_by_key.get(branch_key) if branch_key else None
        branch_item = raw_data.get("current_map_item") or branch_item_by_key or {}

        try:
            logger.info(
                f"DEBUG:MERGE publisher branch_key={branch_key}, branch_item_keys={list(branch_item.keys()) if isinstance(branch_item, dict) else 'N/A'}"
            )
            if isinstance(raw_data.get("importFilingPacks"), list) and isinstance(branch_item, dict):
                match_key = branch_item.get("filingPackId")
                idx = None
                for i, it in enumerate(raw_data.get("importFilingPacks") or []):
                    if isinstance(it, dict) and it.get("filingPackId") == match_key:
                        idx = i
                        break
                if idx is not None:
                    pre_keys = list((raw_data["importFilingPacks"][idx] or {}).keys())
                    logger.info(
                        f"DEBUG:MERGE publisher list_match index={idx}, pre_keys={pre_keys}"
                    )
        except Exception:
            pass

        # Fold branch item into the business context and exclude internals
        folded = merge_branch_context(raw_data, branch_item)

        # Ensure the matching item in importFilingPacks reflects the branch_item (deep merge on the specific element)
        try:
            if isinstance(folded.get("importFilingPacks"), list) and isinstance(branch_item, dict):
                match_key = branch_item.get("filingPackId") or branch_key
                if match_key:
                    packs = list(folded.get("importFilingPacks") or [])
                    for i, it in enumerate(packs):
                        if isinstance(it, dict) and it.get("filingPackId") == match_key:
                            # Deep-merge into the matched pack element
                            def _deep_merge(left: dict, right: dict) -> dict:
                                if not isinstance(left, dict) or not isinstance(right, dict):
                                    return right
                                merged = left.copy()
                                for k, v in right.items():
                                    if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
                                        merged[k] = _deep_merge(merged[k], v)
                                    else:
                                        merged[k] = v
                                return merged
                            pre_keys = list(it.keys())
                            packs[i] = _deep_merge(it, branch_item)
                            logger.info(
                                f"DEBUG:MERGE publisher list_element_merge idx={i}, pre_keys={pre_keys}, post_keys={list(packs[i].keys())}"
                            )
                            folded["importFilingPacks"] = packs
                            break
        except Exception:
            pass

        business_context = {
            k: v for k, v in folded.items()
            if not (isinstance(k, str) and (k.startswith('_') or k == 'current_map_item'))
        }
        messages = business_context.pop("messages", [])

        return {
            "eventId": str(uuid.uuid4()),
            "eventType": event_type,
            "eventTimestamp": datetime.now(timezone.utc).isoformat(),
            "source": "WorkflowOrchestrator",
            "correlationId": context.get("correlationId"),
            "businessContext": business_context,
            "workflowContext": {
                "workflowInstanceId": context.get("workflowInstanceId"),
                "workflowDefinitionURI": context.get("workflow_definition_uri"),
                "consignmentId": state.get("data", {}).get("consignmentId")
            },
            "transition": {
                "currentStep": current_step,
                "nextSteps": next_steps
            },
            "messages": messages
        }
