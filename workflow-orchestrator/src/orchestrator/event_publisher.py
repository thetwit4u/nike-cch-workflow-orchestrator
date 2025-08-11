import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from clients.sns_client import SnsClient
from orchestrator.state import WorkflowState

logger = logging.getLogger(__name__)

class EventPublisher:
    """
    A class to handle the construction and publishing of CHHSystemEvent messages.
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

        message_group_id = context.get("workflowInstanceId")
        if not message_group_id:
            logger.warning(f"workflowInstanceId is missing from context. Cannot publish event.")
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

        business_context = {k: v for k, v in state.get("data", {}).items() if not k.startswith('_')}
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
                "workflowDefinitionURI": context.get("workflow_definition_uri")
            },
            "transition": {
                "currentStep": current_step,
                "nextSteps": next_steps
            },
            "messages": messages
        }
