import json
import logging
from pathlib import Path
from jsonschema import validate, ValidationError
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Load the schema once when the module is imported.
try:
    # Assuming schemas are in a directory relative to this file
    # Adjust the path as needed based on your final project structure.
    # The path navigates from src/utils -> schemas/generic_command.schema.json
    schema_path = Path(__file__).parent.parent.parent / "schemas/generic_command.schema.json"
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
    """A utility class for parsing and validating orchestrator commands."""

    @staticmethod
    def parse_and_validate(command_message: dict) -> bool:
        """
        Validates a command message against the generic_command.schema.json.

        Args:
            command_message: The dictionary representation of the command.

        Returns:
            True if the command is valid, False otherwise.
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

    def create_command_message(self, command_type: str, state_update: dict, next_command: dict) -> dict:
        """
        Creates a command message for external use by the orchestrator,
        like the payload for a scheduled task.
        """
        workflow_instance_id = self.state["context"].get("workflowInstanceId")
        correlation_id = self.state["context"].get("correlationId")

        # Based on the command type, we construct the body differently.
        body = {}
        if command_type == "ASYNC_REQ":
            body = {
                "capability_id": self.node_config.get("capability_id"),
                "input_context": self._extract_keys(self.node_config.get("input_keys")),
                "request_output_keys": self.node_config.get("output_keys")
            }
        
        # Add other command type body structures here if needed

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
                    "original_data": self.state["data"],
                    "body": body
                }
            }
        } 