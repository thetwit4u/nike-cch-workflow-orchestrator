import json
import logging
import os
import uuid
from pathlib import Path
from jsonschema import validate, ValidationError
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Load the schema once when the module is imported.
try:
    # The path navigates from src/utils -> src -> workflow-orchestrator -> cch-workflow-orchestrator -> schemas
    schema_path = Path(__file__).resolve().parent.parent.parent.parent / "schemas/generic_command.schema.json"
    with open(schema_path) as f:
        GENERIC_COMMAND_SCHEMA = json.load(f)
    logger.info("Successfully loaded generic_command.schema.json")
except FileNotFoundError:
    logger.exception("CRITICAL: generic_command.schema.json not found. Validation will fail.")
    GENERIC_COMMAND_SCHEMA = None
except json.JSONDecodeError:
    logger.exception("CRITICAL: Failed to parse generic_command.schema.json. Validation will fail.")
    GENERIC_COMMAND_SCHEMA = None


class CommandParser:
    """A utility class for parsing, validating, and creating orchestrator commands."""

    def __init__(self, state, node_config):
        self.state = state
        self.node_config = node_config

    @staticmethod
    def is_valid_command(command_message: dict) -> bool:
        """
        Validates a command message against the generic_command.schema.json.
        """
        if not GENERIC_COMMAND_SCHEMA:
            logger.error("Validation skipped: Generic command schema is not loaded.")
            return False

        try:
            validate(instance=command_message, schema=GENERIC_COMMAND_SCHEMA)
            logger.info(f"Command with ID '{command_message.get('command', {}).get('id')}' passed validation.")
            return True
        except ValidationError as e:
            logger.error(
                f"Command validation failed for command ID "
                f"'{command_message.get('command', {}).get('id')}': {e.message}"
            )
            return False
        except Exception:
            logger.exception("An unexpected error occurred during command validation.")
            return False

    def create_command_message(self, command_type: str) -> dict:
        """
        Creates a command message to be sent to an external capability.
        """
        header = {
            "workflowInstanceId": self.state["context"].get("workflowInstanceId"),
            "correlationId": self.state["context"].get("correlationId"),
            "commandId": str(uuid.uuid4()),
            "replyToQueueUrl": os.environ.get("COMMAND_QUEUE_URL"),
            "source": "WorkflowOrchestrator",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Add routing hint for parallel branches
        if self.state["context"].get("branch_key"):
            header["routingHint"] = {"branchKey": self.state["context"]["branch_key"]}

        body = {
            "capability_id": self.node_config.get("capability_id"),
            "context": self._extract_keys(self.node_config.get("input_keys")),
            "request_output_keys": self.node_config.get("output_keys"),
        }

        return {"header": header, "body": body}

    def create_internal_command(self, command_type: str, state_update: dict, next_command: dict) -> dict:
        """
        Creates a command message for internal use by the orchestrator,
        like the payload for a scheduled task.
        """
        workflow_instance_id = self.state["context"].get("workflowInstanceId")
        correlation_id = self.state["context"].get("correlationId")

        # This command is for the orchestrator itself.
        # It carries the full context of the original workflow state.
        return {
            "workflowInstanceId": workflow_instance_id,
            "correlationId": correlation_id,
            "workflowDefinitionURI": self.state["context"].get("workflow_definition_uri"),
            "command": {
                "type": command_type,
                "payload": {
                    "state_update": state_update,
                    "next_command": next_command,
                    "original_context": self.state["context"],
                    "original_data": self.state["data"]
                }
            }
        }

    def _extract_keys(self, key_list: list) -> dict:
        """Extracts values from state context and data based on a list of keys."""
        if not key_list:
            return {}
        return {key: self.state["data"].get(key) for key in key_list if key in self.state["data"]} 