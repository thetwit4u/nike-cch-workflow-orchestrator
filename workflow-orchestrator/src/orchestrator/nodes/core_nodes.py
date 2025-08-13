import logging
from orchestrator.state import WorkflowState
from langgraph.checkpoint.base import BaseCheckpointSaver
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

logger = logging.getLogger(__name__)

def handle_event_wait(state: WorkflowState, node_config: dict, node_name: str):
    """
    Handles an 'event_wait' node by pausing graph execution.
    The workflow will wait for an external command (e.g., EVENT_WAIT_RESP) to resume.
    """
    logger.info(f"Executing 'event_wait' node '{node_name}'. Pausing for external event.")
    return interrupt("Pausing for external event.")

def pass_through_action(state: WorkflowState) -> WorkflowState:
    """A no-op action for nodes that only perform routing."""
    return state

def handle_map_fork(state: WorkflowState, node_name: str) -> WorkflowState:
    """
    A node handler for the 'map_fork' node that adds logging before the fork.
    """
    logger.info(f"Executing 'map_fork' node '{node_name}'. The next step should be the map_resolver.")
    return state

def handle_join(state: WorkflowState, destination: str = None, node_name: str = None) -> WorkflowState:
    """
    Node handler for the 'join' node.

    If a 'destination' is provided, this node will perform a "reduce"
    operation by moving aggregated results from the 'map_results' key to
    the destination key in the 'data' dictionary.

    If no 'destination' is provided, it acts as a simple synchronization
    point for parallel branches from a 'fork' node.
    """
    logger.info("Executing join node")
    try:
        if destination:
            # This is a reduce operation for a map_fork
            logger.info(f"Performing reduce operation for map_fork into key '{destination}'.")
            # Move the results from the dedicated map_results key to the final destination
            results = state.get("map_results", [])
            
            keys = destination.split('.')
            current_level = state["data"]
            for part in keys[:-1]:
                current_level = current_level.setdefault(part, {})
            current_level[keys[-1]] = results
            
            logger.info(f"Joined {len(results)} items into 'data.{destination}'.")

            # Clear the map_results for the next map-reduce operation
            state["map_results"] = []
        else:
            # This is a simple synchronization point for a fork
            logger.info("Acting as a synchronization point for fork.")
            # No state change is needed, just pass through.

    except Exception as e:
        logger.exception("Error in handle_join")
        state["error_details"] = {
            # Since we don't have node_config, we can't get the node name here.
            # This can be improved if needed by passing more context.
            "error": str(e)
        }
        state["is_error"] = True

    return state

def handle_register_branch(state: WorkflowState, config: RunnableConfig, checkpointer: BaseCheckpointSaver, node_name: str) -> WorkflowState:
    """
    An internal-only node that registers a parallel branch's checkpoint with the parent.
    This enables routing ASYNC_RESP messages to the correct parallel branch.
    """
    try:
        parent_thread_id = state["context"].get("parent_thread_id")
        branch_key = state["context"].get("branch_key")
        child_thread_id = config["configurable"]["thread_id"]

        if not all([parent_thread_id, branch_key, child_thread_id]):
            raise ValueError("Missing required context for branch registration (parent_thread_id, branch_key).")

        logger.info(f"Registering branch. Key: '{branch_key}', Thread ID: '{child_thread_id}'")

        # Get the parent's checkpoint to get the checkpoint_id
        parent_config_for_get = {"configurable": {"thread_id": parent_thread_id}}
        parent_checkpoint_tuple = checkpointer.get_tuple(parent_config_for_get)

        if not parent_checkpoint_tuple:
            logger.warning(f"Could not find parent checkpoint for thread '{parent_thread_id}'. Skipping branch registration.")
            return state

        parent_checkpoint_id = parent_checkpoint_tuple.checkpoint["id"]

        # Use put_writes for atomic updates on the parent checkpoint
        parent_config_for_put = {"configurable": {"thread_id": parent_thread_id, "checkpoint_id": parent_checkpoint_id}}
        writes = []
        # Map the business branch key to this task's thread id (may equal parent in this model)
        writes.append((f"branch_checkpoints.{branch_key}", child_thread_id))
        # Persist the branch's current map item so responses can restore context without per-branch threads
        current_map_item = state.get("data", {}).get("current_map_item")
        if current_map_item is not None:
            writes.append((f"map_items_by_key.{branch_key}", current_map_item))
        checkpointer.put_writes(parent_config_for_put, writes, child_thread_id)

    except Exception as e:
        logger.exception("Error in handle_register_branch")
        # This is a critical internal error.
        logger.critical(f"CRITICAL: Failed to register branch for node '{node_name}'. Error: {e}", exc_info=True)
        # We must raise an exception here to halt this branch.
        raise

    return state

def handle_log_error(state: WorkflowState, node_config: dict, node_name: str) -> dict:
    """
    Node handler for the 'log_error' type.

    This node logs a structured error message based on its configuration
    and the current error details in the state, then clears the error state.
    """
    try:
        error_code = node_config.get("default_error_code", "UNKNOWN_ERROR")
        default_message = node_config.get("default_message", "An unspecified error occurred.")
        message_key = node_config.get("message_from_context_key")

        # Prioritize the dynamic message from the state if available
        error_details = state.get("error_details") or {}
        dynamic_message = error_details.get(message_key) if message_key else None
        
        message = dynamic_message or error_details.get("error") or default_message

        log_payload = {
            "error_code": error_code,
            "message": message,
            "workflow_instance_id": state["context"].get("workflowInstanceId"),
            "correlation_id": state["context"].get("correlationId"),
            "failed_node": error_details.get("node")
        }

        # The logger adapter will add the workflow_id to the log record
        logger.error(log_payload)

        # Return a patch to clear the error state
        return {
            "is_error": False,
            "error_details": None
        }

    except Exception as e:
        # If the error logger itself fails, log a critical error
        logger.critical(f"CRITICAL: Failed to execute handle_log_error node '{node_name}'. Error: {e}")
        # We still return a patch to clear the state and prevent potential loops
        return {
            "is_error": False,
            "error_details": None
        }


def handle_set_state(state: WorkflowState, node_config: dict, node_name: str) -> dict:
    """
    Handles a 'set_state' node by merging a static dictionary into the 'context' field of the state.
    """
    logger.info(f"Executing 'set_state' node '{node_name}'")
    static_outputs = node_config.get("static_outputs", {})
    if not static_outputs:
        logger.warning(f"'set_state' node '{node_name}' has no 'static_outputs' to apply.")
        return {} # Return an empty patch

    # The 'status' and other metadata belong in the 'context' channel.
    return {"context": static_outputs}

def handle_start_node(state: WorkflowState, node_config: dict, node_name: str) -> WorkflowState:
    """
    A simple pass-through node that marks the start of the workflow.
    """
    logger.info(f"Executing start node '{node_name}'")
    return state

def set_state_node_wrapper(state: WorkflowState, node_config: dict, node_name: str) -> dict:
    """
    Handles 'set_state' nodes, updating the workflow state with static values.
    Also used internally by 'end' nodes to set the final status.
    """
    logger.info(f"Executing set_state node '{node_name}'.")
    static_outputs = node_config.get('static_outputs', {}).copy()

    # Implicitly set status to COMPLETED for any 'end' node.
    # This ensures consistent, predictable behavior for workflow termination.
    if node_config.get('type') == 'end':
        static_outputs['status'] = 'COMPLETED'
    
    # The outputs are merged into the 'data' channel of the state.
    # The current_node tracking is merged into the 'context' channel.
    return {
        "data": static_outputs,
        "context": {"current_node": node_name}
    }


def sync_capability_node_wrapper(state: dict, handler, node_config: dict, node_name: str) -> dict:
    """
    A generic wrapper for synchronous capability nodes that handles input mapping,
    execution, and output mapping without supporting interruption.
    """
    logger.info(f"Executing sync capability node '{node_name}'")
    try:
        # This wrapper will be very similar to the library_node_wrapper, but for capabilities
        # For now, we'll assume a simple pass-through to the handler, which will
        # be responsible for its own state interaction.
        # This can be built out with more generic logic later.
        return handler(state, node_config, node_name)
        
    except Exception as e:
        logger.exception(f"Error executing sync capability node '{node_name}'")
        return {
            "is_error": True,
            "error_details": {"node": node_name, "error": str(e)}
        }
