import json
import logging
import os
import uuid
from pathlib import Path
from jsonschema import validate, ValidationError
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# --- Start of new schema loading logic ---
def load_schema(schema_path: str):
    """Loads a JSON schema file from the new nested directory structure."""
    # The base path for schemas is now consistently inside the 'src' directory
    base_path = Path(__file__).parent.parent / 'schemas' / 'json-schema'
    full_path = base_path / schema_path

    try:
        with open(full_path) as f:
            logger.info(f"Successfully loaded schema '{schema_path}' from: {full_path}")
            return json.load(f)
    except FileNotFoundError:
        logger.critical(f"Schema '{schema_path}' not found at expected path: {full_path}. Validation will fail.")
        return None
    except Exception:
        logger.exception(f"An unexpected error occurred while loading schema: {full_path}")
        return None

# Load the generic command schema from its new location
COMMAND_SCHEMA = load_schema('command/orchestrator/orchestrator-command.schema.json')
# --- End of new schema loading logic ---

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
        if not COMMAND_SCHEMA:
            logger.error("Validation skipped: Generic command schema is not loaded.")
            return False

        try:
            validate(instance=command_message, schema=COMMAND_SCHEMA)
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
        Creates a command message to be sent to an external capability,
        conforming to the capability-command.schema.json (header/body structure).
        """
        context_data = self.state["context"]
        command_queue_url = os.environ.get("COMMAND_QUEUE_URL")
        logger.info(f"Retrieved COMMAND_QUEUE_URL for replyTo: {command_queue_url}")

        command_msg = {
            "header": {
                "workflowInstanceId": context_data.get("workflowInstanceId"),
                "workflowDefinitionURI": context_data.get("workflow_definition_uri"),
                "correlationId": context_data.get("correlationId"),
                "commandId": str(uuid.uuid4()),
                "replyToQueueUrl": command_queue_url,
                "source": "WorkflowOrchestrator",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "body": {
                "capability_id": self.node_config.get("capability_id"),
                "request_output_keys": self.node_config.get("request_output_keys"),
                "context": self._extract_keys(self.node_config.get("input_keys")),
            }
        }

        # Add routing hint for parallel branches if applicable
        if context_data.get("branch_key"):
            command_msg["header"]["routingHint"] = {"branchKey": context_data.get("branch_key")}

        return command_msg

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
        
        extracted = {}
        for key in key_list:
            if key in self.state.get("data", {}):
                extracted[key] = self.state["data"][key]
            elif key in self.state.get("context", {}):
                extracted[key] = self.state["context"][key]
            else:
                logger.warning(f"Key '{key}' not found in state data or context during extraction.")
        
        return extracted 