import logging
import os
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any
from functools import partial

import boto3
from botocore.exceptions import ClientError

from clients.queue_client import QueueClient
from utils.command_parser import CommandParser
from clients.scheduler_client import SchedulerClient
import dateutil.parser

from langgraph.graph import Interrupt

from orchestrator.state import WorkflowState

logger = logging.getLogger(__name__)

# Initialize clients and config loader
queue_client = QueueClient()
scheduler_client = boto3.client('scheduler')
iam_client = boto3.client('iam')

# Environment variables for queue URLs, set by the CDK
REPLY_QUEUE_URL = os.environ.get("REPLY_QUEUE_URL")
IMPORT_QUEUE_URL = os.environ.get("IMPORT_QUEUE_URL")
EXPORT_QUEUE_URL = os.environ.get("EXPORT_QUEUE_URL")

# A simple map to get the correct queue URL for a capability
CAPABILITY_QUEUE_MAP = {
    "import#enrichment": IMPORT_QUEUE_URL,
    "export#enrichment": EXPORT_QUEUE_URL,
    # Add other capability mappings here
}


def _base_action(state: Dict[str, Any], config: Dict[str, Any], node_config: Dict[str, Any], action_logic_fn) -> Dict[str, Any]:
    """A generic wrapper for node actions."""
    instance_id = config.get("configurable", {}).get("thread_id", "unknown_instance")
    logger.info(f"Executing node '{node_config.get('title')}' for instance '{instance_id}'")
    
    # Make the instance_id available to the action logic if needed
    current_context = state.get("context", {})
    current_context['workflowInstanceId'] = instance_id
    current_context['__thread_id'] = instance_id  # For LangGraph compatibility

    # Execute the specific logic for the node type
    updated_context = action_logic_fn(state, node_config)
    
    # If the logic function returns an interrupt, propagate it
    if isinstance(updated_context, Interrupt):
        return updated_context
    # If the logic function returns None (e.g., for a fire-and-forget async call),
    # it means no state update is needed. Return None to signal pause.
    if updated_context is None:
        return None

    # Otherwise, return the patch to be applied to the state's context
    return {"context": updated_context}


def handle_sync_call(state: WorkflowState, node_config: dict, node_name: str) -> WorkflowState:
    """
    Handles a 'sync_call' node by pausing execution.
    The workflow will wait for an external 'HITL_RESP' command to resume.
    """
    try:
        logger.info(f"Executing 'sync_call' node '{node_name}'. Pausing for Human-in-the-Loop response.")
        # We must interrupt execution here to wait for the HITL_RESP.
        # A real implementation would also send a message to a UI or notification service.
        raise Interrupt()
    except Exception as e:
        logger.error(f"Error in 'sync_call' node '{node_name}': {e}")
        state["is_error"] = True
        state["error_details"] = {"error": str(e), "node": node_name}
    return state


def handle_async_request(state: WorkflowState, node_config: dict, node_name: str) -> WorkflowState:
    """
    Handles 'async_request' nodes by sending a command to a capability queue and then pausing.
    """
    try:
        logger.info(f"Executing 'async_request' node '{node_name}' with config: {node_config}")
        
        command_parser = CommandParser(state, node_config)
        command_payload = command_parser.create_command_message(command_type="ASYNC_REQ")

        capability_id = node_config.get("capability_id")
        queue_name = os.environ.get(f"CCH_CAPABILITY_{capability_id.upper()}_QUEUE_NAME")
        if not queue_name:
            raise ValueError(f"Queue for capability '{capability_id}' is not configured in environment variables.")

        queue_client = QueueClient()
        queue_client.send_message(
            queue_name=queue_name,
            message_body=command_payload
        )
        logger.info(f"Successfully sent async request for capability '{capability_id}'. Pausing for response.")
        
        # We must interrupt execution here to wait for the async response
        raise Interrupt()

    except Exception as e:
        logger.error(f"Error in 'async_request' node '{node_name}': {e}")
        state["is_error"] = True
        state["error_details"] = {"error": str(e), "node": node_name}
    return state


def handle_scheduled_request(state: WorkflowState, node_config: dict, node_name: str) -> WorkflowState:
    """
    Handles 'scheduled_request' nodes by creating a one-time EventBridge schedule
    that sends a command back to the orchestrator to execute at a future time.
    It can also update the state immediately upon schedule creation.
    """
    try:
        logger.info(f"Executing 'scheduled_request' node '{node_name}' with config: {node_config}")
        
        # 1. Immediate state update (if defined)
        on_schedule_set = node_config.get("on_schedule_set")
        if on_schedule_set:
            state["data"].update(on_schedule_set)
            logger.info(f"Updated state with on_schedule_set payload: {on_schedule_set}")

        # 2. Initialize clients and get schedule parameters
        scheduler_client = SchedulerClient()
        schedule_params = node_config.get("schedule_parameters", {})
        date_key = schedule_params.get("date_context_key")
        if not date_key:
            raise ValueError("'date_context_key' is missing in schedule_parameters")
            
        date_str = state["data"].get(date_key)
        if not date_str:
            raise ValueError(f"Context key '{date_key}' not found in state data.")
        
        schedule_time = dateutil.parser.isoparse(date_str)
        schedule_time_str = schedule_time.strftime('%Y-%m-%dT%H:%M:%S')

        # 3. Define the actual async request to be sent when the schedule fires
        next_command_payload = {
            "capability_id": node_config.get("capability_id"),
            "input_keys": node_config.get("input_keys"),
            "output_keys": node_config.get("request_output_keys"), # Use the new dedicated key
            "on_response": node_config.get("on_response")
        }

        # 4. Construct the internal "wake-up" command
        command_parser = CommandParser(state, node_config)
        internal_command = command_parser.create_internal_command(
            command_type="EXECUTE_SCHEDULED_TASK",
            state_update={}, # The state update is now immediate, not deferred.
            next_command=next_command_payload
        )

        # 5. Determine the orchestrator's own queue and schedule name
        orchestrator_queue_arn = os.environ.get("COMMAND_QUEUE_ARN")
        if not orchestrator_queue_arn:
            raise ValueError("Orchestrator's own COMMAND_QUEUE_ARN is not configured.")

        workflow_instance_id = state['context']['workflowInstanceId']
        schedule_name = f"{workflow_instance_id}-{node_name}"

        # 6. Create or update the one-time schedule
        scheduler_client.create_or_update_onetime_schedule(
            schedule_name=schedule_name,
            schedule_time=schedule_time_str,
            target_arn=orchestrator_queue_arn,
            payload=internal_command
        )
        
        logger.info(f"Successfully scheduled internal task for node '{node_name}' at {schedule_time_str}.")

    except Exception as e:
        logger.error(f"Error in 'scheduled_request' node '{node_name}': {e}")
        state["is_error"] = True
        state["error_details"] = {"error": str(e), "node": node_name}
    
    return state


# --- Factory Functions ---

def create_async_request_action(node_config: Dict[str, Any]):
    """Creates a node action function for an async_request."""
    return partial(_base_action, node_config=node_config, action_logic_fn=handle_async_request)


def create_sync_call_action(node_config: Dict[str, Any]):
    """Creates a node action function for a sync_call."""
    return partial(_base_action, node_config=node_config, action_logic_fn=handle_sync_call)
