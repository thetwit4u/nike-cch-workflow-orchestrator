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

    def publish_event(self, state: dict, current_step: Dict[str, Any], next_steps: list[Dict[str, Any]], status: str, workflow_definition: Dict[str, Any] | None = None) -> None:
        """
        Constructs and publishes a CchSystemEvent for a completed workflow step.
        """
        self._publish(state, current_step, next_steps, status, workflow_definition)

    def publish_start_event(self, initial_state: dict, next_steps: list[Dict[str, Any]]) -> None:
        """
        Constructs and publishes a CchSystemEvent for the start of a workflow.
        """
        current_step = {"name": "WorkflowStart", "title": "Workflow Started", "type": "start"}
        self._publish(initial_state, current_step, next_steps, "Started")

    def _publish(self, state: dict, current_step: Optional[Dict[str, Any]], next_steps: List[Dict[str, Any]], status: str, workflow_definition: Dict[str, Any] | None = None) -> None:
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

        event = self._build_event(state, current_step, next_steps, status, workflow_definition)

        try:
            self.sns_client.publish_message(
                topic_arn=self.topic_arn,
                message_body=event,
                message_group_id=message_group_id
            )
        except Exception as e:
            logger.error(f"Failed to publish event: {e}", exc_info=True)

    def _build_event(self, state: dict, current_step: Optional[Dict[str, Any]], next_steps: List[Dict[str, Any]], status: str, workflow_definition: Dict[str, Any] | None = None) -> Dict[str, Any]:
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

        # removed publisher merge debug logs

        # Fold branch item into the business context and exclude internals
        folded = merge_branch_context(raw_data, branch_item)

        # If we have a workflow definition, dynamically resolve map_fork config to identify the parent list key
        parent_list_key = None
        branch_id_key = None
        try:
            if workflow_definition and current_step:
                nodes = (workflow_definition.get("nodes", {}) or {})
                # Find a map_fork whose branch_entry_node eventually leads to current_step.name or whose entry directly matches
                for node_name, node_def in nodes.items():
                    if node_def.get("type") == "map_fork":
                        entry = node_def.get("branch_entry_node")
                        if entry and (entry == current_step.get("name")):
                            parent_list_key = node_def.get("input_list_key")
                            branch_id_key = node_def.get("branch_key")
                            break
                # Fallback: if no direct entry match, pick the only map_fork if unique
                if not parent_list_key:
                    forks = [nd for nd in nodes.values() if nd.get("type") == "map_fork"]
                    if len(forks) == 1:
                        parent_list_key = forks[0].get("input_list_key")
                        branch_id_key = forks[0].get("branch_key")
        except Exception:
            parent_list_key = None
            branch_id_key = None

        # Ensure the matching item in the resolved parent list reflects the branch_item (deep merge on the specific element)
        try:
            if parent_list_key and isinstance(folded.get(parent_list_key), list) and isinstance(branch_item, dict):
                match_key = branch_item.get(branch_id_key) or branch_key
                if match_key:
                    packs = list(folded.get(parent_list_key) or [])
                    for i, it in enumerate(packs):
                        if isinstance(it, dict) and it.get(branch_id_key) == match_key:
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
                            packs[i] = _deep_merge(it, branch_item)
                            folded[parent_list_key] = packs
                            break
        except Exception:
            pass

        business_context = {
            k: v for k, v in folded.items()
            if not (isinstance(k, str) and (k.startswith('_') or k == 'current_map_item'))
        }

        # If this is branch-scoped, eliminate branch-item keys from top-level except globals and the parent list key
        if branch_key or (isinstance(branch_item, dict) and bool(branch_item)):
            allowed_globals = {"messages", "status"}
            if parent_list_key:
                allowed_globals.add(parent_list_key)
            dropped = []
            if isinstance(branch_item, dict):
                for key in list(business_context.keys()):
                    if key in allowed_globals:
                        continue
                    if key in branch_item:
                        business_context.pop(key, None)
                        dropped.append(key)
            # removed publisher drop-top-level debug logs
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
