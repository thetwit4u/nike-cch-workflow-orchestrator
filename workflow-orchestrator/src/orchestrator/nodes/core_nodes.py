import logging
from ..state import WorkflowState
from langgraph.checkpoint.base import BaseCheckpointSaver
from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)

def pass_through_action(state: WorkflowState) -> WorkflowState:
    """A no-op action for nodes that only perform routing."""
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

        # Get the parent's checkpoint
        parent_config = {"configurable": {"thread_id": parent_thread_id}}
        parent_checkpoint = checkpointer.get(parent_config)

        if not parent_checkpoint:
            # This can happen in rare race conditions. We will let the next run resolve it.
            logger.warning(f"Could not find parent checkpoint for thread '{parent_thread_id}'. Skipping branch registration.")
            return state

        # Update the parent's checkpoint with the new branch info
        branch_checkpoints = parent_checkpoint["channel_values"].get("branch_checkpoints", {})
        branch_checkpoints[branch_key] = child_thread_id
        
        update = {"branch_checkpoints": branch_checkpoints}
        
        # Write the updated checkpoint back
        checkpointer.put(parent_config, {"channel_values": update}, None)

    except Exception as e:
        logger.exception("Error in handle_register_branch")
        # This is a critical internal error.
        logger.critical(f"CRITICAL: Failed to register branch for node '{node_name}'. Error: {e}", exc_info=True)
        # We must raise an exception here to halt this branch.
        raise

    return state

def handle_log_error(state: WorkflowState, node_config: dict, node_name: str) -> WorkflowState:
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
        error_details = state.get("error_details", {})
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

        # Clear the error state now that it has been handled
        state["is_error"] = False
        state["error_details"] = None

    except Exception as e:
        # If the error logger itself fails, log a critical error
        logger.critical(f"CRITICAL: Failed to execute handle_log_error node '{node_name}'. Error: {e}")
        # We still clear the state to prevent potential loops
        state["is_error"] = False
        state["error_details"] = None

    return state
