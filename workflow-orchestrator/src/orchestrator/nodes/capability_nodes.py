import logging
import os
import json
from typing import Dict, Any
from functools import partial
from clients.queue_client import QueueClient
from langgraph.types import interrupt, Interrupt

logger = logging.getLogger(__name__)

# This would be a shared client instance in a real app
queue_client = QueueClient()

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


def _async_request_logic(state: Dict[str, Any], node_config: Dict[str, Any]) -> Dict[str, Any]:
    """The specific logic for handling an 'async_request' node."""
    context = state.get("context", {})
    capability_id = node_config.get('capability_id')
    target_queue_url = CAPABILITY_QUEUE_MAP.get(capability_id)
    workflow_id = context.get('workflow_id')
    instance_id = context.get('workflowInstanceId')

    # Preserve the original correlationId if present, otherwise fall back to instance_id
    correlation_id = context.get('correlationId', instance_id)

    # Get the output key(s) this node is waiting for
    output_keys = node_config.get("request_output_keys", [])
    missing_keys = [key for key in output_keys if key not in context]
    if not missing_keys:
        logger.info(f"Async output key(s) {output_keys} already present in context. Skipping async request for instance '{instance_id}'.")
        return None
    elif context.get('resumed', False):
        # If this is a resume and keys are still missing, raise error
        error_msg = f"Missing required async output key(s) after resume: {missing_keys} for instance '{instance_id}'"
        logger.error(error_msg)
        raise ValueError(error_msg)

    if not all([target_queue_url, workflow_id, instance_id]):
        error_msg = f"Missing critical information for async request: queue='{target_queue_url}', workflow_id='{workflow_id}', instance_id='{instance_id}'"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Pass the definition URI so the capability can return it for stateless resume
    workflow_definition_uri = state.get("workflow_definition_uri")
    if not workflow_definition_uri:
        raise ValueError("Cannot send async request: 'workflow_definition_uri' not found in state.")

    # Remove __thread_id from the context before sending to the capability service
    context_to_send = {k: v for k, v in context.items() if k != '__thread_id'}

    # Construct the message payload
    message = {
        "header": {
            "correlationId": correlation_id,
            "replyToQueueUrl": REPLY_QUEUE_URL,
            "source": "WorkflowOrchestrator",
            "workflowDefinitionURI": workflow_definition_uri,
        },
        "body": {
            "capability_id": capability_id,
            "context": context_to_send
        }
    }

    # Log the sending of the ASYNC command in the same format as other SQS logs
    logger.info(f"[clients.queue_client] Sending message to queue: {target_queue_url} for capability: {capability_id} [WorkflowID: {instance_id}] Message: {json.dumps(message)}")

    # Send the message to the capability queue
    queue_client.send_message(target_queue_url, message)
    
    logger.info(f"Sent async request for '{capability_id}'. Workflow will pause and wait for response.")
    return interrupt("Waiting for async response")


def _sync_call_logic(state: Dict[str, Any], node_config: Dict[str, Any]) -> Dict[str, Any]:
    """The specific logic for handling a 'sync_call' node (placeholder)."""
    context = state.get("context", {})
    capability_id = node_config.get('capability_id')
    logger.info(f"Executing sync call for capability '{capability_id}'")

    # In a real implementation, this would involve a direct call (e.g., HTTP request)
    # or invoking another internal function.
    
    # For the PoC, we will simulate a successful result and update the context.
    output_keys = node_config.get('output_keys', [])
    
    if output_keys:
        # Create mock output for the keys this node is supposed to produce
        mock_output_key = output_keys[0]
        context[mock_output_key] = {
            "status": "processed",
            "data": f"mock_data_from_{capability_id}"
        }
        logger.info(f"Mocked sync call result: {context}")

    return context


# --- Factory Functions ---

def create_async_request_action(node_config: Dict[str, Any]):
    """Creates a node action function for an async_request."""
    return partial(_base_action, node_config=node_config, action_logic_fn=_async_request_logic)


def create_sync_call_action(node_config: Dict[str, Any]):
    """Creates a node action function for a sync_call."""
    return partial(_base_action, node_config=node_config, action_logic_fn=_sync_call_logic)
