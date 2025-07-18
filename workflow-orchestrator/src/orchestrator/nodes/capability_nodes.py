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
from clients.http_client import HttpClient
from utils.command_parser import CommandParser
from clients.scheduler_client import SchedulerClient
import dateutil.parser

from langgraph.types import Interrupt, interrupt

from orchestrator.state import WorkflowState
from .library_nodes import _get_required_param

logger = logging.getLogger(__name__)

# Initialize clients and config loader
queue_client = QueueClient()
http_client = HttpClient()
scheduler_client = boto3.client('scheduler')
iam_client = boto3.client('iam')

# Environment variables for queue URLs, set by the CDK






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
        return interrupt()
    except Exception as e:
        logger.error(f"Error in 'sync_call' node '{node_name}': {e}")
        state["is_error"] = True
        state["error_details"] = {"error": str(e), "node": node_name}
    return state


def handle_async_request(state: WorkflowState, node_config: dict, node_name: str):
    """
    Handles 'async_request' nodes by sending a command to a capability queue and then pausing.
    """
    try:
        logger.info(f"Executing 'async_request' node '{node_name}' with config: {node_config}")
        
        capability_id = _get_required_param(node_config, "capability_id")
        
        # The parser needs the full state to access both 'data' and 'context'
        parser = CommandParser(state, node_config)
        # The create_command_message function already creates the full message envelope
        full_command_message = parser.create_command_message(command_type="ASYNC_REQ")

        # Determine the endpoint, prioritizing the debug queue if available
        debug_queue_url = os.environ.get('DEBUG_CAPABILITY_QUEUE_URL')

        if debug_queue_url:
            queue_url = debug_queue_url
            logger.info(f"DEBUG MODE: Routing capability request to debug queue: {queue_url}")
        else:
            service_name = capability_id.split('#')[0].upper()
            endpoint_key_1 = f"CCH_MOCK_HTTP_ENDPOINT_{service_name}"
            endpoint_key_2 = f"CCH_CAPABILITY_{service_name}"
            
            queue_url = os.environ.get(endpoint_key_1) or os.environ.get(endpoint_key_2)

            if not queue_url:
                raise ValueError(f"No capability endpoint configured for service '{service_name}'. "
                                 f"Set either {endpoint_key_1} or {endpoint_key_2}.")
        
        queue_client = QueueClient()
        queue_client.send_message(queue_url, full_command_message)
        
        logger.info(f"Successfully sent async request for capability '{capability_id}'. Pausing for response.")
        
        # Correctly interrupt the graph to wait for the external ASYNC_RESP.
        return interrupt("Waiting for async capability response.")

    except (ValueError, ClientError) as e:
        # Catch specific operational errors, but allow GraphInterrupt to propagate.
        error_message = f"Error in 'async_request' node '{node_name}': {e}"
        logger.error(error_message, exc_info=True)
        state["is_error"] = True
        state["error_details"] = {"error": error_message, "node": node_name}
        return state


def handle_scheduled_request(state: WorkflowState, node_config: dict, node_name: str) -> WorkflowState:
    """
    Handles 'scheduled_request' nodes by creating a one-time EventBridge schedule
    that sends a command back to the orchestrator to execute at a future time.
    It can also update the state immediately upon schedule creation.
    """
    try:
        # If this is a user-initiated update, we don't interrupt. We let the graph
        # flow to the conditional node which will then loop back to recalculate.
        if state.get("data", {}).get("_user_update_request"):
            logger.info(f"User update detected in '{node_name}'. Bypassing interrupt to allow recalculation loop.")
            # Consume the flag by creating a new data dict without it
            new_data = state.get("data", {}).copy()
            new_data.pop("_user_update_request", None)
            return {"data": new_data} # Return patch

        # IDEMPOTENCY CHECK: Use a node-specific internal flag to see if this node
        # is being re-invoked after its scheduled task has already fired.
        idempotency_key = f"_{node_name}_resumed"
        if state.get("context", {}).get(idempotency_key):
            logger.info(f"Resuming from '{node_name}' after scheduled task has executed. Proceeding.")
            return state # The work is done, just pass the state along.

        logger.info(f"Executing 'scheduled_request' node '{node_name}' with config: {node_config}")
        
        # 1. Initialize clients and get schedule parameters
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

        # 2. Define the actual async request to be sent when the schedule fires
        next_command_payload = {
            "capability_id": node_config.get("capability_id"),
            "input_keys": node_config.get("input_keys"),
            "output_keys": node_config.get("request_output_keys"), # Use the new dedicated key
            "on_response": node_config.get("on_response")
        }

        # 3. Construct the internal "wake-up" command, embedding the state update payload
        command_parser = CommandParser(state, node_config)
        
        # Start with the business-logic payload and add our internal idempotency flag
        state_update_payload = node_config.get("on_schedule_set", {})
        state_update_payload[idempotency_key] = True

        internal_command = command_parser.create_internal_command(
            command_type="EXECUTE_SCHEDULED_TASK",
            state_update=state_update_payload, # Embed it here
            next_command=next_command_payload
        )

        # 4. Determine the orchestrator's own queue and schedule name
        orchestrator_queue_arn = os.environ.get("COMMAND_QUEUE_ARN")
        if not orchestrator_queue_arn:
            raise ValueError("Orchestrator's own COMMAND_QUEUE_ARN is not configured.")

        workflow_instance_id = state['context']['workflowInstanceId']
        schedule_name = f"{workflow_instance_id}-{node_name}"

        # 5. Create or update the one-time schedule, passing the full current state
        # The scheduler client will use this to check for test overrides.
        scheduler_client.create_or_update_onetime_schedule(
            schedule_name=schedule_name,
            schedule_time=schedule_time_str,
            target_arn=orchestrator_queue_arn,
            payload=internal_command,
            state_context=state # Pass the full state here
        )
        
        logger.info(f"Successfully scheduled internal task for node '{node_name}' at {schedule_time_str}. Pausing workflow.")
        
        # Interrupt the graph to wait for the scheduled task to fire.
        # This is a terminal action for this function path.
        return interrupt("Pausing to wait for the scheduled task to execute.")

    except (ClientError, ValueError) as e:
        # This block should only catch actual operational errors, not control-flow
        # exceptions like Interrupt.
        logger.error(f"Error in 'scheduled_request' node '{node_name}': {e}", exc_info=True)
        state["is_error"] = True
        state["error_details"] = {"error": str(e), "node": node_name}
        return state # On error, we return the updated state to proceed to the 'on_failure' node.


# --- Factory Functions ---

def create_async_request_action(node_config: Dict[str, Any]):
    """Creates a node action function for an async_request."""
    return partial(_base_action, node_config=node_config, action_logic_fn=handle_async_request)


def create_sync_call_action(node_config: Dict[str, Any]):
    """Creates a node action function for a sync_call."""
    return partial(_base_action, node_config=node_config, action_logic_fn=handle_sync_call)
