import os
import uuid
import json
import logging
from datetime import datetime, timezone

import pytest

# Configure logging
logger = logging.getLogger(__name__)

# The local data that will be uploaded to S3 for the test
HAPPY_PATH_DATA = {
    "consignment": {
        "plannedDischargeDate": "2025-07-15T10:00:00Z"
    }
}

@pytest.fixture(scope="module")
def happy_path_s3_uri(aws_client, cdk_outputs, stack_name):
    """Fixture to upload the happy path data to S3 and provide the URI."""
    data_bucket = cdk_outputs.get_output(stack_name, "IngestBucketName")
    s3_object_key = f"test-data/{uuid.uuid4()}.json"
    
    aws_client.upload_json_to_s3(data_bucket, s3_object_key, HAPPY_PATH_DATA)
    logger.info(f"Uploaded happy path test data to s3://{data_bucket}/{s3_object_key}")
    
    return f"s3://{data_bucket}/{s3_object_key}"


def test_simplified_workflow_happy_path(aws_client, cdk_outputs, stack_name, workflow_verifier, happy_path_s3_uri):
    """
    Tests the happy path of the simplified import workflow.
    """
    # --- ARRANGE ---
    thread_id = f"test-run-happy-{uuid.uuid4()}"
    
    # 1. Upload the workflow definition
    definitions_bucket = cdk_outputs.get_output(stack_name, "DefinitionsBucketName")
    local_filepath = "workflow-definitions/import_us_v1.1.1-simplified.yaml"
    s3_workflow_key = os.path.basename(local_filepath)
    aws_client.upload_to_s3(local_filepath, definitions_bucket, s3_workflow_key)
    workflow_definition_uri = f"s3://{definitions_bucket}/{s3_workflow_key}"
    
    # 2. Prepare the START command
    command_queue_url = cdk_outputs.get_output(stack_name, "OrchestratorCommandQueueUrl")
    command_message = {
        "workflowInstanceId": thread_id,
        "correlationId": thread_id,
        "workflowDefinitionURI": workflow_definition_uri,
        "command": {
            "type": "EVENT",
            "id": str(uuid.uuid4()),
            "source": "Pytest-HappyPath",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "deliverySetURI": happy_path_s3_uri,
                "filingpackCreationStarted": False,
                "_no_cache": True
            }
        }
    }

    # --- ACT ---
    aws_client.send_sqs_message(command_queue_url, command_message)

    # --- ASSERT ---
    final_state = workflow_verifier.poll_for_final_state(
        thread_id,
        lambda state: state.get("data", {}).get("status") == "COMPLETED"
    )
    
    assert final_state, f"Workflow {thread_id} did not complete."
    assert final_state.get("data", {}).get("otfDate") == "2025-07-05T10:00:00Z"
    
    final_context = final_state.get("context", {})
    assert final_context.get("current_node") == "End_Workflow"
    logger.info("Successfully verified happy path completion and otfDate calculation.")


def test_simplified_workflow_s3_error_path(aws_client, cdk_outputs, stack_name, workflow_verifier):
    """
    Tests the error path of the simplified workflow when the S3 file is missing.
    """
    # --- ARRANGE ---
    thread_id = f"test-run-error-{uuid.uuid4()}"
    
    # 1. Upload the workflow definition
    definitions_bucket = cdk_outputs.get_output(stack_name, "DefinitionsBucketName")
    local_filepath = "workflow-definitions/import_us_v1.1.1-simplified.yaml"
    s3_workflow_key = os.path.basename(local_filepath)
    aws_client.upload_to_s3(local_filepath, definitions_bucket, s3_workflow_key)
    workflow_definition_uri = f"s3://{definitions_bucket}/{s3_workflow_key}"
    
    # This URI points to a file that does not exist
    non_existent_s3_uri = f"s3://{cdk_outputs.get_output(stack_name, 'IngestBucketName')}/non-existent-file.json"

    # 2. Prepare the START command
    command_queue_url = cdk_outputs.get_output(stack_name, "OrchestratorCommandQueueUrl")
    command_message = {
        "workflowInstanceId": thread_id,
        "correlationId": thread_id,
        "workflowDefinitionURI": workflow_definition_uri,
        "command": {
            "type": "EVENT",
            "id": str(uuid.uuid4()),
            "source": "Pytest-ErrorPath",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "deliverySetURI": non_existent_s3_uri,
                "filingpackCreationStarted": False,
                "_no_cache": True
            }
        }
    }

    # --- ACT ---
    aws_client.send_sqs_message(command_queue_url, command_message)

    # --- ASSERT ---
    # The workflow should still 'complete' by running the error handler path
    final_state = workflow_verifier.poll_for_final_state(
        thread_id,
        lambda state: state.get("data", {}).get("status") == "COMPLETED"
    )
    
    assert final_state, f"Workflow {thread_id} did not complete via the error path."
    
    # In the error path, otfDate should NOT be present
    assert "otfDate" not in final_state.get("data", {}), "otfDate should not be present in an error scenario."

    # The final node should still be End_Workflow, reached via Handle_Error
    final_context = final_state.get("context", {})
    assert final_context.get("current_node") == "End_Workflow"
    logger.info("Successfully verified that the S3 error path completes correctly.") 