import logging
import os
import pytest
import uuid
from datetime import datetime, timezone

# Configure logging
logger = logging.getLogger(__name__)

def test_run_trivial_workflow(aws_client, cdk_outputs, stack_name, workflow_verifier):
    """
    Tests the end-to-end execution of a trivial workflow.

    This test verifies that the orchestrator can:
    1. Receive a command from SQS.
    2. Fetch a workflow definition from S3.
    3. Execute the workflow graph.
    4. Persist the final state to DynamoDB.
    """
    # --- ARRANGE ---
    # 1. Upload the workflow definition to S3
    definitions_bucket = cdk_outputs.get_output(stack_name, "DefinitionsBucketName")
    local_filepath = "workflow-definitions/trivial_workflow.yaml"
    s3_object_key = os.path.basename(local_filepath)
    s3_uri = f"s3://{definitions_bucket}/{s3_object_key}"

    assert os.path.exists(local_filepath), f"Local file not found: {local_filepath}"
    aws_client.upload_to_s3(local_filepath, definitions_bucket, s3_object_key)
    logger.info(f"Uploaded workflow definition to {s3_uri}")

    # 2. Prepare the START command
    thread_id = f"test-run-{uuid.uuid4()}"
    command_queue_url = cdk_outputs.get_output(stack_name, "OrchestratorCommandQueueUrl")
    command_message = {
        "workflowInstanceId": thread_id,
        "workflowName": "Trivial Test Workflow",
        "correlationId": thread_id,
        "workflowDefinitionURI": s3_uri,
        "command": {
            "type": "EVENT",
            "id": str(uuid.uuid4()),
            "source": "Pytest",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {}
        }
    }
    logger.info(f"Prepared START command for workflowInstanceId: {thread_id}")

    # --- ACT ---
    # 3. Send the command to the SQS queue
    aws_client.send_sqs_message(command_queue_url, command_message)
    logger.info("Sent START command to SQS queue.")

    # --- ASSERT ---
    # 4. Poll DynamoDB for the final 'COMPLETED' status
    logger.info(f"Polling for final state for workflow {thread_id}...")
    final_state = workflow_verifier.poll_for_final_state(
        thread_id=thread_id,
        timeout_seconds=30,
        condition_fn=lambda state: state.get("data", {}).get("status") == "COMPLETED"
    )

    # Assert that the workflow completed successfully
    assert final_state, f"Workflow {thread_id} did not complete successfully."
    assert final_state.get("data", {}).get("status") == "COMPLETED"

    logger.info(f"Successfully verified completion of workflow {thread_id}.")

    # 5. Verify the final node was 'End_Workflow'
    final_state = workflow_verifier.get_latest_state(thread_id)
    assert final_state, "Could not retrieve final state for verification."
    
    final_context = final_state.get("channel_values", {}).get("context", {})
    final_node = final_context.get("current_node")
    assert final_node == "End_Workflow", f"Expected final node to be 'End_Workflow', but got '{final_node}'"
    
    logger.info("Trivial workflow execution verified successfully.") 