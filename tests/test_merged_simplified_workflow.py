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
    "deliverySet": {
        "deliverySetId": str(uuid.uuid4()),
        "consignment": {
            "billOfLadingNbr": "TESTBOL12345",
            "plannedDischargeDate": "2025-07-15T10:00:00Z",
            "totalShipmentCount": 1,
            "transportVehicle": {
                "modeOfTransportation": "SEA",
                "vehicleId": "VESSEL123",
                "voyageNumber": "V001"
            },
            "carrier": {
                "id": "CARRIER001",
                "name": "Test Carrier Ltd"
            },
            "originAddress": {
                "addressLine1": "123 Test St",
                "city": "Test City",
                "provinceStateRegionCode": "TC",
                "country": "US",
                "postalCode": "12345"
            },
            "dischargeAddress": {
                "addressLine1": "456 Discharge Ave",
                "city": "Discharge City",
                "provinceStateRegionCode": "DC",
                "country": "US",
                "postalCode": "67890"
            }
        },
        "deliveries": [
            {
                "deliveryNoteNbr": "DN123456",
                "shipmentNbr": "SH001",
                "manufacturingCountryOfOrigin": "VN",
                "importerOfRecord": {
                    "id": "IOR001",
                    "name": "Test Importer",
                    "bondDetails": {
                        "customsBondNumber": "BOND123456"
                    }
                },
                "declarant": {
                    "id": "DEC001",
                    "name": "Test Declarant"
                },
                "deliveryItems": [
                    {
                        "deliveryNoteItemNbr": "ITEM001",
                        "quantity": 100,
                        "crPoNbr": "PO123456",
                        "productDetails": {
                            "code": "PROD001",
                            "size": "L",
                            "grossWeightInGrams": 1500,
                            "netWeightInGrams": 1400
                        },
                        "valuation": {
                            "valuationMethod": "TRANSACTION_VALUE",
                            "baseValue": {
                                "amount": 250.00,
                                "currency": "USD"
                            }
                        },
                        "commodityBreakDowns": [
                            {
                                "type": "TEXTILE",
                                "commodities": [
                                    {
                                        "harmonizedCode": "6109.10.00",
                                        "harmonizedCodePercentage": 100.0,
                                        "dutyPercentage": 8.5,
                                        "vatPercentage": 0.0
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]
    }
}

ERROR_PATH_DATA = {
    "deliverySet": {
        "deliverySetId": str(uuid.uuid4()),
        "consignment": {
            "billOfLadingNbr": "ERRORBOL12345",
            "plannedDischargeDate": "invalid-date-format",
            "totalShipmentCount": 1
        },
        "deliveries": []
    }
}

@pytest.fixture(scope="module")
def happy_path_s3_uri(aws_client, cdk_outputs, stack_name):
    """Fixture to upload the happy path data to S3 and provide the URI."""
    data_bucket = cdk_outputs.get_output(stack_name, "IngestBucketName")
    s3_object_key = f"test-data/merged-workflow-happy-{uuid.uuid4()}.json"
    
    aws_client.upload_json_to_s3(data_bucket, s3_object_key, HAPPY_PATH_DATA)
    logger.info(f"Uploaded happy path test data to s3://{data_bucket}/{s3_object_key}")
    
    return f"s3://{data_bucket}/{s3_object_key}"

@pytest.fixture(scope="module")
def error_path_s3_uri(aws_client, cdk_outputs, stack_name):
    """Fixture to upload the error path data to S3 and provide the URI."""
    data_bucket = cdk_outputs.get_output(stack_name, "IngestBucketName")
    s3_object_key = f"test-data/merged-workflow-error-{uuid.uuid4()}.json"
    
    aws_client.upload_json_to_s3(data_bucket, s3_object_key, ERROR_PATH_DATA)
    logger.info(f"Uploaded error path test data to s3://{data_bucket}/{s3_object_key}")
    
    return f"s3://{data_bucket}/{s3_object_key}"

def test_merged_workflow_happy_path(aws_client, cdk_outputs, stack_name, workflow_verifier, happy_path_s3_uri):
    """
    Tests the happy path of the merged simplified workflow.
    Tests that the single async step performs both enrichment and filing pack creation.
    """
    # --- ARRANGE ---
    thread_id = f"test-merged-happy-{uuid.uuid4()}"
    
    # 1. Upload the workflow definition
    definitions_bucket = cdk_outputs.get_output(stack_name, "DefinitionsBucketName")
    local_filepath = "workflow-definitions/import_us_v1.1.1-simplified.yaml"
    s3_workflow_key = os.path.basename(local_filepath)
    aws_client.upload_to_s3(local_filepath, definitions_bucket, s3_workflow_key)
    workflow_definition_uri = f"s3://{definitions_bucket}/{s3_workflow_key}"
    
    # 2. Extract deliverySetId from the test data
    delivery_set_id = HAPPY_PATH_DATA["deliverySet"]["deliverySetId"]
    
    # 3. Prepare the START command
    command_queue_url = cdk_outputs.get_output(stack_name, "OrchestratorCommandQueueUrl")
    command_message = {
        "workflowInstanceId": thread_id,
        "correlationId": thread_id,
        "workflowDefinitionURI": workflow_definition_uri,
        "command": {
            "type": "EVENT",
            "id": str(uuid.uuid4()),
            "source": "Pytest-MergedHappyPath",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "deliverySetId": delivery_set_id,
                "deliverySetURI": happy_path_s3_uri,
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
    
    # Verify that the workflow completed successfully
    final_context = final_state.get("context", {})
    final_data = final_state.get("data", {})
    assert final_context.get("current_node") == "End_Workflow"
    
    # Verify that the merged step outputs are present in the data field
    assert "deliverySetImportEnrichedId" in final_data
    assert "deliverySetImportEnrichedURI" in final_data
    assert "deliverySetImportEnrichedStatus" in final_data
    assert "importFilingPacksStatus" in final_data
    assert "importFilingPacks" in final_data
    
    # Verify the merged step was successful
    assert final_data.get("deliverySetImportEnrichedStatus") == "SUCCESS"
    assert final_data.get("importFilingPacksStatus") == "SUCCESS"
    
    logger.info("Successfully verified merged workflow happy path completion with both enrichment and filing pack creation.")

def test_merged_workflow_error_path(aws_client, cdk_outputs, stack_name, workflow_verifier, error_path_s3_uri):
    """
    Tests the error path of the merged simplified workflow.
    Tests that error handling works correctly for the single async step.
    """
    # --- ARRANGE ---
    thread_id = f"test-merged-error-{uuid.uuid4()}"
    
    # 1. Upload the workflow definition
    definitions_bucket = cdk_outputs.get_output(stack_name, "DefinitionsBucketName")
    local_filepath = "workflow-definitions/import_us_v1.1.1-simplified.yaml"
    s3_workflow_key = os.path.basename(local_filepath)
    aws_client.upload_to_s3(local_filepath, definitions_bucket, s3_workflow_key)
    workflow_definition_uri = f"s3://{definitions_bucket}/{s3_workflow_key}"
    
    # 2. Extract deliverySetId from the test data
    delivery_set_id = ERROR_PATH_DATA["deliverySet"]["deliverySetId"]
    
    # 3. Prepare the START command
    command_queue_url = cdk_outputs.get_output(stack_name, "OrchestratorCommandQueueUrl")
    command_message = {
        "workflowInstanceId": thread_id,
        "correlationId": thread_id,
        "workflowDefinitionURI": workflow_definition_uri,
        "command": {
            "type": "EVENT",
            "id": str(uuid.uuid4()),
            "source": "Pytest-MergedErrorPath",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "deliverySetId": delivery_set_id,
                "deliverySetURI": error_path_s3_uri,
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
    
    # The final node should be End_Workflow, reached via Handle_Error
    final_context = final_state.get("context", {})
    assert final_context.get("current_node") == "End_Workflow"
    
    # Verify that error information is captured
    assert "deliverySetImportEnrichedError" in final_context or "filingPacksError" in final_context
    
    logger.info("Successfully verified that the merged workflow error path completes correctly.")

def test_merged_workflow_missing_s3_file(aws_client, cdk_outputs, stack_name, workflow_verifier):
    """
    Tests the error path when the S3 file is missing.
    """
    # --- ARRANGE ---
    thread_id = f"test-merged-missing-s3-{uuid.uuid4()}"
    
    # 1. Upload the workflow definition
    definitions_bucket = cdk_outputs.get_output(stack_name, "DefinitionsBucketName")
    local_filepath = "workflow-definitions/import_us_v1.1.1-simplified.yaml"
    s3_workflow_key = os.path.basename(local_filepath)
    aws_client.upload_to_s3(local_filepath, definitions_bucket, s3_workflow_key)
    workflow_definition_uri = f"s3://{definitions_bucket}/{s3_workflow_key}"
    
    # This URI points to a file that does not exist
    non_existent_s3_uri = f"s3://{cdk_outputs.get_output(stack_name, 'IngestBucketName')}/non-existent-merged-file.json"

    # 2. Prepare the START command
    command_queue_url = cdk_outputs.get_output(stack_name, "OrchestratorCommandQueueUrl")
    command_message = {
        "workflowInstanceId": thread_id,
        "correlationId": thread_id,
        "workflowDefinitionURI": workflow_definition_uri,
        "command": {
            "type": "EVENT",
            "id": str(uuid.uuid4()),
            "source": "Pytest-MergedMissingS3",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "deliverySetId": str(uuid.uuid4()),
                "deliverySetURI": non_existent_s3_uri,
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
    
    assert final_state, f"Workflow {thread_id} did not complete via the error path."
    
    # The final node should be End_Workflow, reached via Handle_Error
    final_context = final_state.get("context", {})
    assert final_context.get("current_node") == "End_Workflow"
    
    logger.info("Successfully verified that the merged workflow handles missing S3 file correctly.")

def test_merged_workflow_performance(aws_client, cdk_outputs, stack_name, workflow_verifier, happy_path_s3_uri):
    """
    Tests the performance of the merged workflow compared to the original two-step approach.
    This test verifies that the merged step completes in reasonable time.
    """
    # --- ARRANGE ---
    thread_id = f"test-merged-performance-{uuid.uuid4()}"
    
    # 1. Upload the workflow definition
    definitions_bucket = cdk_outputs.get_output(stack_name, "DefinitionsBucketName")
    local_filepath = "workflow-definitions/import_us_v1.1.1-simplified.yaml"
    s3_workflow_key = os.path.basename(local_filepath)
    aws_client.upload_to_s3(local_filepath, definitions_bucket, s3_workflow_key)
    workflow_definition_uri = f"s3://{definitions_bucket}/{s3_workflow_key}"
    
    # 2. Extract deliverySetId from the test data
    delivery_set_id = HAPPY_PATH_DATA["deliverySet"]["deliverySetId"]
    
    # 3. Prepare the START command
    command_queue_url = cdk_outputs.get_output(stack_name, "OrchestratorCommandQueueUrl")
    command_message = {
        "workflowInstanceId": thread_id,
        "correlationId": thread_id,
        "workflowDefinitionURI": workflow_definition_uri,
        "command": {
            "type": "EVENT",
            "id": str(uuid.uuid4()),
            "source": "Pytest-MergedPerformance",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "deliverySetId": delivery_set_id,
                "deliverySetURI": happy_path_s3_uri,
                "_no_cache": True
            }
        }
    }

    # --- ACT ---
    start_time = datetime.now(timezone.utc)
    aws_client.send_sqs_message(command_queue_url, command_message)

    # --- ASSERT ---
    final_state = workflow_verifier.poll_for_final_state(
        thread_id,
        lambda state: state.get("data", {}).get("status") == "COMPLETED",
        timeout_seconds=300  # 5 minute timeout
    )
    
    end_time = datetime.now(timezone.utc)
    execution_time = (end_time - start_time).total_seconds()
    
    assert final_state, f"Workflow {thread_id} did not complete within timeout."
    assert execution_time < 300, f"Workflow took too long to complete: {execution_time} seconds"
    
    logger.info(f"Merged workflow completed in {execution_time} seconds.")
    
    # Verify successful completion
    final_context = final_state.get("context", {})
    assert final_context.get("current_node") == "End_Workflow"
    assert final_context.get("deliverySetImportEnrichedStatus") == "SUCCESS"
    assert final_context.get("importFilingPacksStatus") == "SUCCESS"
    
    logger.info("Successfully verified merged workflow performance.")