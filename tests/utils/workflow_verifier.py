import time
import logging
import msgpack
from .aws_client import AWSClient

logger = logging.getLogger(__name__)

class WorkflowVerifier:
    """
    Verifies the state of a workflow by polling the DynamoDB checkpoint table.
    """

    def __init__(self, aws_client: AWSClient, table_name: str):
        """
        Initializes the verifier.

        Args:
            aws_client: An instance of the AWSClient.
            table_name: The name of the DynamoDB table storing workflow state.
        """
        self.aws_client = aws_client
        self.table_name = table_name

    def _decode_checkpoint(self, item: dict) -> dict:
        """Decodes the msgpack-encoded checkpoint data from a DynamoDB item."""
        if 'checkpoint' not in item:
            logger.warning("No 'checkpoint' field found in the DynamoDB item.")
            return {}
        
        # The checkpoint is stored as DynamoDB's Binary type, which Boto3
        # deserializes into a `boto3.dynamodb.types.Binary` object. We need to
        # access its `.value` attribute to get the raw bytes for msgpack.
        checkpoint_bytes = item['checkpoint'].value
        
        try:
            # We need to set use_list=False because langgraph serializes tuples,
            # and the default is to deserialize them as lists.
            decoded_state = msgpack.unpackb(checkpoint_bytes, raw=False)
            return decoded_state
        except Exception as e:
            logger.error(f"Failed to decode msgpack checkpoint: {e}")
            return {}

    def poll_for_state_update(self, thread_id: str, assertion_fn, timeout_seconds: int = 30, interval_seconds: int = 5) -> bool:
        """
        Polls the DynamoDB table until a state assertion function returns True or a timeout is reached.

        Args:
            thread_id: The ID of the workflow thread to check.
            assertion_fn: A function that takes the workflow state (DynamoDB item) and returns
                          True if the desired condition is met, False otherwise.
            timeout_seconds: The maximum time to wait for the condition to be met.
            interval_seconds: The time to wait between polling attempts.

        Returns:
            True if the assertion passed within the timeout, False otherwise.
        """
        logger.info(f"Polling for state update for thread_id '{thread_id}' in table '{self.table_name}'...")
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            state_item = self.aws_client.query_most_recent_checkpoint(self.table_name, thread_id)
            if state_item:
                decoded_state = self._decode_checkpoint(state_item)
                if not decoded_state:
                    logger.warning("Checkpoint was empty after decoding. Continuing to poll.")
                    time.sleep(interval_seconds)
                    continue
                
                logger.info(f"Polling... Current decoded state: {decoded_state}")
                    
                try:
                    if assertion_fn(decoded_state):
                        logger.info("Assertion passed.")
                        return True
                except Exception as e:
                    logger.warning(f"Assertion function raised an exception: {e}. Continuing to poll.")
            else:
                logger.warning("No state item found during polling attempt.")
            
            logger.info(f"Condition not met. Waiting for {interval_seconds} seconds before next poll.")
            time.sleep(interval_seconds)

        logger.error(f"Timeout reached after {timeout_seconds} seconds. Assertion never passed.")
        
        # --- Debugging: Log all checkpoints on failure ---
        logger.info("--- DUMPING ALL CHECKPOINTS FOR FAILED WORKFLOW ---")
        all_checkpoints = self.aws_client.query_all_checkpoints(self.table_name, thread_id)
        if not all_checkpoints:
            logger.warning("No checkpoints found for this workflow instance.")
        else:
            for i, item in enumerate(all_checkpoints):
                decoded = self._decode_checkpoint(item)
                logger.info(f"--- CHECKPOINT {i} ---")
                logger.info(decoded)
                logger.info("--------------------")
        
        return False

    def poll_for_final_state(self, thread_id: str, condition_fn, timeout_seconds: int = 30, interval_seconds: int = 5) -> dict | None:
        """
        Polls the DynamoDB table until a condition function returns True, then returns the final state.

        Args:
            thread_id: The ID of the workflow thread to check.
            condition_fn: A function that takes the decoded state and returns True if the
                          condition is met.
            timeout_seconds: The maximum time to wait.
            interval_seconds: The interval between polls.

        Returns:
            The final state dictionary if the condition was met, otherwise None.
        """
        logger.info(f"Polling for final state for thread_id '{thread_id}' in table '{self.table_name}'...")
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            state_item = self.aws_client.query_most_recent_checkpoint(self.table_name, thread_id)
            if state_item:
                decoded_state = self._decode_checkpoint(state_item)
                if not decoded_state:
                    time.sleep(interval_seconds)
                    continue
                
                logger.info(f"Polling... Current decoded state: {decoded_state.get('channel_values')}")
                
                try:
                    # We pass the full decoded state to the condition function
                    if condition_fn(decoded_state.get('channel_values', {})):
                        logger.info("Final state condition met.")
                        return decoded_state.get('channel_values')
                except Exception as e:
                    logger.warning(f"Condition function raised an exception: {e}. Continuing to poll.")
            
            logger.info(f"Condition not met. Waiting for {interval_seconds} seconds before next poll.")
            time.sleep(interval_seconds)

        logger.error(f"Timeout reached after {timeout_seconds} seconds. Condition never met.")
        # ... (rest of the debugging dump logic can be similar to poll_for_state_update)
        return None

    def get_latest_state(self, thread_id: str) -> dict:
        """
        Retrieves and decodes the most recent checkpoint for a given workflow instance.

        Args:
            thread_id: The ID of the workflow thread.

        Returns:
            A dictionary representing the decoded state, or an empty dict if not found.
        """
        logger.info(f"Getting latest state for thread_id '{thread_id}'")
        state_item = self.aws_client.query_most_recent_checkpoint(self.table_name, thread_id)
        if not state_item:
            logger.warning("No state item found.")
            return {}
        
        return self._decode_checkpoint(state_item)

    def assert_node_reached(self, thread_id: str, node_name: str, timeout_seconds: int = 30):
        """
        Asserts that the workflow has reached a specific node.

        This checks the 'current_node' field in the workflow's context.
        """
        def assertion(state):
            # The actual structure will depend on how LangGraph Checkpoint is configured.
            # This assumes a structure like: {'context': {'current_node': 'node_name'}}
            # This might need to be adjusted once the orchestrator is fully implemented.
            context = state.get('context', {})
            current_node = context.get('current_node')
            logger.info(f"Checking if current node '{current_node}' matches expected '{node_name}'")
            return current_node == node_name

        assert self.poll_for_state_update(thread_id, assertion, timeout_seconds), \
            f"Workflow did not reach node '{node_name}' within {timeout_seconds} seconds."

    def assert_context_contains(self, thread_id: str, key: str, value, timeout_seconds: int = 30):
        """
        Asserts that a specific key-value pair exists in the workflow's context.
        """
        def assertion(state):
            context = state.get('context', {})
            logger.info(f"Checking if context key '{key}' has value '{value}'. Current value: '{context.get(key)}'")
            return context.get(key) == value

        assert self.poll_for_state_update(thread_id, assertion, timeout_seconds), \
            f"Context key '{key}' with value '{value}' not found within {timeout_seconds} seconds." 