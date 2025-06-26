import time
import logging
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
            state_item = self.aws_client.get_dynamodb_item(self.table_name, key={"thread_id": thread_id})
            if state_item:
                try:
                    if assertion_fn(state_item):
                        logger.info("Assertion passed.")
                        return True
                except Exception as e:
                    logger.warning(f"Assertion function raised an exception: {e}. Continuing to poll.")
            
            logger.info(f"Condition not met. Waiting for {interval_seconds} seconds before next poll.")
            time.sleep(interval_seconds)

        logger.error(f"Timeout reached after {timeout_seconds} seconds. Assertion never passed.")
        return False

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