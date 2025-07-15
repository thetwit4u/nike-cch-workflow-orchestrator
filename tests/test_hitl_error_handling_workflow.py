import os
import uuid
import json
import logging
from datetime import datetime, timezone

import pytest

# Configure logging
logger = logging.getLogger(__name__)

# Replicating the user's typo "consignment" for keys, but using "consignment" for the main object
# to match the data structure the mock service will expect after S3 fetch.
HITL_ERROR_PATH_DATA = {
    "consignment": {
        "consignmentId": str(uuid.uuid4()),
        "billOfLadingNbr": "HITL-TRIGGER",
        "plannedDischargeDate": "2025-08-01T10:00:00Z",
        "totalShipmentCount": 1
    }
}

NON_RECOVERABLE_ERROR_DATA = {
    "consignment": {
        "consignmentId": str(uuid.uuid4()),
        "billOfLadingNbr": "NON-RECOVERABLE-ERROR",
        "plannedDischargeDate": "2025-08-02T10:00:00Z",
        "totalShipmentCount": 1
    }
}

@pytest.fixture(scope="module")
def hitl_s3_uri(aws_client, cdk_outputs, stack_name):
    """Fixture to upload the HITL test data to S3 and provide the URI."""
    data_bucket = cdk_outputs.get_output(stack_name, "IngestBucketName")
    s3_object_key = f"test-data/hitl-workflow-error-{uuid.uuid4()}.json"
    
    aws_client.upload_json_to_s3(data_bucket, s3_object_key, HITL_ERROR_PATH_DATA)
    logger.info(f"Uploaded HITL test data to s3://{data_bucket}/{s3_object_key}")
    
    return f"s3://{data_bucket}/{s3_object_key}"

@pytest.fixture(scope="module")
def non_recoverable_s3_uri(aws_client, cdk_outputs, stack_name):
    """Fixture to upload the non-recoverable error data to S3."""
    data_bucket = cdk_outputs.get_output(stack_name, "IngestBucketName")
    s3_object_key = f"test-data/non-recoverable-error-{uuid.uuid4()}.json"
    
    aws_client.upload_json_to_s3(data_bucket, s3_object_key, NON_RECOVERABLE_ERROR_DATA)
    logger.info(f"Uploaded non-recoverable test data to s3://{data_bucket}/{s3_object_key}")
    
    return f"s3://{data_bucket}/{s3_object_key}"

def test_hitl_error_handling_and_retry(aws_client, cdk_outputs, stack_name, workflow_verifier, hitl_s3_uri):
    """
    Tests the full Human-in-the-Loop (HITL) error handling and retry loop.
    1. Triggers a recoverable error in the 'Create_Filing_Packs' step.
    2. Verifies the workflow pauses at the 'Wait_For_HITL_Resolution' node.
    3. Sends an 'HITL_RESP' command to resume the workflow.
    4. Verifies the workflow retries the step and completes successfully.
    """
    # --- ARRANGE ---
    thread_id = f"test-hitl-error-retry-{uuid.uuid4()}"
    
    # 1. Upload the HITL workflow definition
    definitions_bucket = cdk_outputs.get_output(stack_name, "DefinitionsBucketName")
    local_filepath = "workflow-definitions/import_us_v1.1.1-simplified-errorhandling.yaml"
    s3_workflow_key = os.path.basename(local_filepath)
    aws_client.upload_to_s3(local_filepath, definitions_bucket, s3_workflow_key)
    workflow_definition_uri = f"s3://{definitions_bucket}/{s3_workflow_key}"

    # 2. Prepare and send the initial START command
    # The mock service is now hardcoded to return an error for "HITL-TRIGGER"
    command_queue_url = cdk_outputs.get_output(stack_name, "OrchestratorCommandQueueUrl")
    start_command = {
        "workflowInstanceId": thread_id,
        "correlationId": thread_id,
        "workflowDefinitionURI": workflow_definition_uri,
        "command": {
            "type": "EVENT",
            "id": str(uuid.uuid4()),
            "source": "Pytest-HITLTest-Start",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            # Status is not required for EVENT type as per schema
            "payload": {
                "consignmentId": HITL_ERROR_PATH_DATA["consignment"]["consignmentId"],
                "consignmentURI": hitl_s3_uri,
                "_no_cache": True
            }
        }
    }
    aws_client.send_sqs_message(command_queue_url, start_command)

    # --- ASSERT (Workflow Paused) ---
    paused_state = workflow_verifier.poll_for_specific_node(
        thread_id,
        "Wait_For_HITL_Resolution",
        timeout_seconds=120
    )
    assert paused_state, f"Workflow {thread_id} did not pause at Wait_For_HITL_Resolution."
    
    paused_data = paused_state.get("data", {})
    assert paused_data.get("status") == "ERROR"
    assert "messages" in paused_data
    assert paused_data["messages"][0]["code"] == "HITL_REQUIRED"
    logger.info(f"Successfully verified workflow {thread_id} is paused for HITL.")

    # --- ARRANGE (For Resume) ---
    # Prepare and send the HITL_RESP command to resume the workflow
    resume_command = {
        "workflowInstanceId": thread_id,
        "correlationId": thread_id,
        "workflowDefinitionURI": workflow_definition_uri,
        "command": {
            "type": "HITL_RESP",
            "id": str(uuid.uuid4()),
            "source": "Pytest-HITLTest-Resume",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "SUCCESS", # Signal that the manual step was successful
            "payload": {
                "resolution_notes": "Manual intervention completed by test automation."
            }
        }
    }

    # --- ACT (Resume) ---
    aws_client.send_sqs_message(command_queue_url, resume_command)
    logger.info(f"Sent HITL_RESP command to resume workflow {thread_id}.")

    # --- ASSERT (Workflow Completed) ---
    final_state = workflow_verifier.poll_for_final_state(
        thread_id,
        lambda state: state.get("data", {}).get("status") == "COMPLETED",
        timeout_seconds=180
    )
    
    assert final_state, f"Workflow {thread_id} did not complete after HITL resolution."
    
    final_context = final_state.get("context", {})
    final_data = final_state.get("data", {})
    
    # Verify the workflow completed successfully after the retry
    assert final_context.get("current_node") == "End_Workflow"
    assert final_data.get("status") == "COMPLETED" # Final status from the End_Workflow node
    assert "importFilingPacks" in final_data
    # The error message should not be present in the final state
    assert "messages" not in final_data
    
    logger.info("Successfully verified HITL error handling, retry, and successful completion.")

def test_workflow_handles_non_recoverable_error(aws_client, cdk_outputs, stack_name, workflow_verifier, non_recoverable_s3_uri):
    """
    Tests that the workflow correctly handles a non-recoverable (non-HITL) error
    and follows the generic error handling path.
    """
    # --- ARRANGE ---
    thread_id = f"test-non-recoverable-error-{uuid.uuid4()}"

    # 1. Upload the workflow definition
    definitions_bucket = cdk_outputs.get_output(stack_name, "DefinitionsBucketName")
    local_filepath = "workflow-definitions/import_us_v1.1.1-simplified-errorhandling.yaml"
    s3_workflow_key = os.path.basename(local_filepath)
    aws_client.upload_to_s3(local_filepath, definitions_bucket, s3_workflow_key)
    workflow_definition_uri = f"s3://{definitions_bucket}/{s3_workflow_key}"
    
    # 2. Prepare the START command
    command_queue_url = cdk_outputs.get_output(stack_name, "OrchestratorCommandQueueUrl")
    start_command = {
        "workflowInstanceId": thread_id,
        "correlationId": thread_id,
        "workflowDefinitionURI": workflow_definition_uri,
        "command": {
            "type": "EVENT",
            "id": str(uuid.uuid4()),
            "source": "Pytest-NonRecoverableError",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "consignmentId": NON_RECOVERABLE_ERROR_DATA["consignment"]["consignmentId"],
                "consignmentURI": non_recoverable_s3_uri,
                "_no_cache": True
            }
        }
    }
    
    # --- ACT ---
    aws_client.send_sqs_message(command_queue_url, start_command)
    
    # --- ASSERT ---
    # The workflow should still 'complete' by running the _default error handler path
    final_state = workflow_verifier.poll_for_final_state(
        thread_id,
        lambda state: state.get("data", {}).get("status") == "COMPLETED"
    )
    
    assert final_state, f"Workflow {thread_id} did not complete via the default error path."
    
    # Verify the final node is End_Workflow
    final_context = final_state.get("context", {})
    assert final_context.get("current_node") == "End_Workflow"
    
    # Verify that the error information from the failed step was persisted
    final_data = final_state.get("data", {})
    assert final_data.get("status") == "ERROR"
    assert "messages" in final_data
    assert final_data["messages"][0]["code"] == "VALIDATION_ERROR"
    
    logger.info("Successfully verified that the workflow handles non-recoverable errors correctly.")

# --- New Test Data and Fixture for Happy Path ---

HAPPY_PATH_DATA_FOR_HITL = {
    "consignment": {
        "consignmentId": str(uuid.uuid4()),
        "billOfLadingNbr": "HAPPY-PATH-BOL",  # A non-triggering value
        "plannedDischargeDate": "2025-08-03T10:00:00Z",
        "totalShipmentCount": 1
    }
}

@pytest.fixture(scope="module")
def happy_path_s3_uri_for_hitl(aws_client, cdk_outputs, stack_name):
    """Fixture to upload happy path data for the HITL workflow test."""
    data_bucket = cdk_outputs.get_output(stack_name, "IngestBucketName")
    s3_object_key = f"test-data/hitl-workflow-happy-{uuid.uuid4()}.json"
    
    aws_client.upload_json_to_s3(data_bucket, s3_object_key, HAPPY_PATH_DATA_FOR_HITL)
    logger.info(f"Uploaded HITL happy path test data to s3://{data_bucket}/{s3_object_key}")
    
    return f"s3://{data_bucket}/{s3_object_key}"


def test_hitl_workflow_happy_path(aws_client, cdk_outputs, stack_name, workflow_verifier, happy_path_s3_uri_for_hitl):
    """
    Tests the straight-through happy path for the HITL-enabled workflow definition.
    This ensures the completion check works in the simplest case.
    """
    # --- ARRANGE ---
    thread_id = f"test-hitl-happy-path-{uuid.uuid4()}"
    
    definitions_bucket = cdk_outputs.get_output(stack_name, "DefinitionsBucketName")
    local_filepath = "workflow-definitions/import_us_v1.1.1-simplified-errorhandling.yaml"
    s3_workflow_key = os.path.basename(local_filepath)
    aws_client.upload_to_s3(local_filepath, definitions_bucket, s3_workflow_key)
    workflow_definition_uri = f"s3://{definitions_bucket}/{s3_workflow_key}"

    command_queue_url = cdk_outputs.get_output(stack_name, "OrchestratorCommandQueueUrl")
    start_command = {
        "workflowInstanceId": thread_id,
        "correlationId": thread_id,
        "workflowDefinitionURI": workflow_definition_uri,
        "command": {
            "type": "EVENT",
            "id": str(uuid.uuid4()),
            "source": "Pytest-HappyPath",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "consignmentId": HAPPY_PATH_DATA_FOR_HITL["consignment"]["consignmentId"],
                "consignmentURI": happy_path_s3_uri_for_hitl,
                "_no_cache": True
            }
        }
    }
    
    # --- ACT ---
    aws_client.send_sqs_message(command_queue_url, start_command)
    
    # --- ASSERT ---
    final_state = workflow_verifier.poll_for_final_state(
        thread_id,
        lambda state: state.get("data", {}).get("status") == "COMPLETED",
        timeout_seconds=120
    )
    
    assert final_state, f"Workflow {thread_id} did not complete on the happy path."
    
    final_context = final_state.get("context", {})
    final_data = final_state.get("data", {})
    
    assert final_context.get("current_node") == "End_Workflow"
    assert final_data.get("importFilingPacksStatus") == "SUCCESS"
    logger.info("Successfully verified HITL workflow happy path completion.") 