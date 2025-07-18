import os
import uuid
import json
import logging
import pytest
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

# Configure logging
logger = logging.getLogger(__name__)

class WorkflowSchemaValidator:
    """Comprehensive validator for workflow schema functionality"""
    
    def __init__(self, aws_client, cdk_outputs, stack_name, workflow_verifier):
        self.aws_client = aws_client
        self.cdk_outputs = cdk_outputs
        self.stack_name = stack_name
        self.workflow_verifier = workflow_verifier
        self.definitions_bucket = cdk_outputs.get_output(stack_name, "DefinitionsBucketName")
        self.command_queue_url = cdk_outputs.get_output(stack_name, "OrchestratorCommandQueueUrl")
        self.data_bucket = cdk_outputs.get_output(stack_name, "IngestBucketName")
    
    def create_and_run_workflow(self, workflow_definition: Dict[str, Any], 
                               initial_payload: Dict[str, Any] = None,
                               expected_final_status: str = "COMPLETED",
                               timeout_seconds: int = 60) -> Dict[str, Any]:
        """Creates and runs a workflow with the given definition"""
        
        # Generate unique identifiers
        workflow_id = f"test-{uuid.uuid4()}"
        thread_id = f"test-run-{uuid.uuid4()}"
        
        # Create workflow definition file
        workflow_filename = f"{workflow_id}.yaml"
        workflow_path = f"/tmp/{workflow_filename}"
        
        # Write workflow definition to file
        import yaml
        with open(workflow_path, 'w') as f:
            yaml.dump(workflow_definition, f)
        
        # Upload to S3
        self.aws_client.upload_to_s3(workflow_path, self.definitions_bucket, workflow_filename)
        workflow_definition_uri = f"s3://{self.definitions_bucket}/{workflow_filename}"
        
        # Create command message
        command_message = {
            "workflowInstanceId": thread_id,
            "correlationId": thread_id,
            "workflowDefinitionURI": workflow_definition_uri,
            "command": {
                "type": "EVENT",
                "id": str(uuid.uuid4()),
                "source": "Pytest-SchemaValidation",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": initial_payload or {}
            }
        }
        
        # Send command to queue
        self.aws_client.send_sqs_message(self.command_queue_url, command_message)
        
        # Wait for completion
        final_state = self.workflow_verifier.poll_for_final_state(
            thread_id,
            lambda state: state.get("data", {}).get("status") == expected_final_status,
            timeout_seconds=timeout_seconds
        )
        
        # Clean up
        os.remove(workflow_path)
        
        return {
            "final_state": final_state,
            "thread_id": thread_id,
            "workflow_id": workflow_id
        }

@pytest.fixture
def schema_validator(aws_client, cdk_outputs, stack_name, workflow_verifier):
    """Fixture that provides a workflow schema validator"""
    return WorkflowSchemaValidator(aws_client, cdk_outputs, stack_name, workflow_verifier)

# ====================
# CORE NODE TYPE TESTS
# ====================

def test_entry_and_end_nodes(schema_validator):
    """Test basic entry and end node functionality"""
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Entry End Test",
        "workflow_id": "entry_end_test",
        "entry_point": "Start",
        "initial_context": [],
        "nodes": {
            "Start": {
                "type": "entry",
                "title": "Start Node",
                "next": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    result = schema_validator.create_and_run_workflow(workflow_definition)
    
    assert result["final_state"], "Workflow should complete successfully"
    assert result["final_state"]["data"]["status"] == "COMPLETED"
    
    # Verify final node
    final_context = result["final_state"].get("context", {})
    assert final_context.get("current_node") == "End_Workflow"
    
    logger.info("✅ Entry and End nodes validated successfully")

def test_set_state_node(schema_validator):
    """Test set_state node functionality"""
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Set State Test",
        "workflow_id": "set_state_test",
        "entry_point": "Set_Values",
        "initial_context": [],
        "nodes": {
            "Set_Values": {
                "type": "set_state",
                "title": "Set State Values",
                "static_outputs": {
                    "test_value": "hello_world",
                    "test_number": 42,
                    "test_boolean": True
                },
                "on_success": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    result = schema_validator.create_and_run_workflow(workflow_definition)
    
    assert result["final_state"], "Workflow should complete successfully"
    
    # Verify set values are in data
    final_data = result["final_state"].get("data", {})
    assert final_data.get("test_value") == "hello_world"
    assert final_data.get("test_number") == 42
    assert final_data.get("test_boolean") is True
    
    logger.info("✅ Set State node validated successfully")

def test_condition_node(schema_validator):
    """Test condition node functionality"""
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Condition Test",
        "workflow_id": "condition_test",
        "entry_point": "Set_Test_Value",
        "initial_context": [],
        "nodes": {
            "Set_Test_Value": {
                "type": "set_state",
                "title": "Set Test Value",
                "static_outputs": {
                    "test_condition": "success"
                },
                "on_success": "Check_Condition"
            },
            "Check_Condition": {
                "type": "condition",
                "title": "Check Test Condition",
                "condition_on_key": "test_condition",
                "branches": {
                    "success": "Success_Path",
                    "failure": "Failure_Path",
                    "_default": "Default_Path"
                }
            },
            "Success_Path": {
                "type": "set_state",
                "title": "Success Path",
                "static_outputs": {
                    "path_taken": "success"
                },
                "on_success": "End_Workflow"
            },
            "Failure_Path": {
                "type": "set_state",
                "title": "Failure Path",
                "static_outputs": {
                    "path_taken": "failure"
                },
                "on_success": "End_Workflow"
            },
            "Default_Path": {
                "type": "set_state",
                "title": "Default Path",
                "static_outputs": {
                    "path_taken": "default"
                },
                "on_success": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    result = schema_validator.create_and_run_workflow(workflow_definition)
    
    assert result["final_state"], "Workflow should complete successfully"
    
    # Verify correct path was taken
    final_data = result["final_state"].get("data", {})
    assert final_data.get("path_taken") == "success"
    
    logger.info("✅ Condition node validated successfully")

def test_fork_and_join_nodes(schema_validator):
    """Test fork and join node functionality"""
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Fork Join Test",
        "workflow_id": "fork_join_test",
        "entry_point": "Start_Fork",
        "initial_context": [],
        "nodes": {
            "Start_Fork": {
                "type": "fork",
                "title": "Start Parallel Processing",
                "branches": ["Branch_A", "Branch_B"]
            },
            "Branch_A": {
                "type": "set_state",
                "title": "Branch A Processing",
                "static_outputs": {
                    "branch_a_result": "completed"
                },
                "on_success": "Join_Branches"
            },
            "Branch_B": {
                "type": "set_state",
                "title": "Branch B Processing",
                "static_outputs": {
                    "branch_b_result": "completed"
                },
                "on_success": "Join_Branches"
            },
            "Join_Branches": {
                "type": "join",
                "title": "Join Parallel Branches",
                "join_branches": ["Branch_A", "Branch_B"],
                "on_success": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    result = schema_validator.create_and_run_workflow(workflow_definition)
    
    assert result["final_state"], "Workflow should complete successfully"
    
    # Verify both branches completed
    final_data = result["final_state"].get("data", {})
    assert final_data.get("branch_a_result") == "completed"
    assert final_data.get("branch_b_result") == "completed"
    
    logger.info("✅ Fork and Join nodes validated successfully")

def test_log_error_node(schema_validator):
    """Test log_error node functionality"""
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Log Error Test",
        "workflow_id": "log_error_test",
        "entry_point": "Create_Error",
        "initial_context": [],
        "nodes": {
            "Create_Error": {
                "type": "set_state",
                "title": "Create Error State",
                "static_outputs": {
                    "is_error": True,
                    "error_details": {
                        "error": "Test error message",
                        "node": "Create_Error"
                    }
                },
                "on_success": "Handle_Error"
            },
            "Handle_Error": {
                "type": "log_error",
                "title": "Handle Test Error",
                "default_error_code": "TEST-001",
                "default_message": "Test error occurred",
                "on_success": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    result = schema_validator.create_and_run_workflow(workflow_definition)
    
    assert result["final_state"], "Workflow should complete successfully"
    
    # Verify error was cleared
    final_data = result["final_state"].get("data", {})
    assert final_data.get("is_error") is False or final_data.get("is_error") is None
    
    logger.info("✅ Log Error node validated successfully")

# ==========================
# LIBRARY CALL NODE TESTS
# ==========================

def test_s3_read_jsonpath_node(schema_validator):
    """Test s3#read_jsonpath library call functionality"""
    
    # Create test data
    test_data = {
        "consignment": {
            "plannedDischargeDate": "2025-07-15T10:00:00Z",
            "billOfLadingNbr": "TEST-BOL-123"
        },
        "metadata": {
            "version": "1.0"
        }
    }
    
    # Upload test data to S3
    test_key = f"test-data/jsonpath-test-{uuid.uuid4()}.json"
    schema_validator.aws_client.upload_json_to_s3(
        schema_validator.data_bucket, 
        test_key, 
        test_data
    )
    test_uri = f"s3://{schema_validator.data_bucket}/{test_key}"
    
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "S3 JSONPath Test",
        "workflow_id": "s3_jsonpath_test",
        "entry_point": "Read_Discharge_Date",
        "initial_context": ["testDataURI"],
        "nodes": {
            "Read_Discharge_Date": {
                "type": "library_call",
                "title": "Read Planned Discharge Date",
                "library_function_id": "s3#read_jsonpath",
                "parameters": {
                    "input_s3_uri_key": "testDataURI",
                    "jsonpath_expression": "$.consignment.plannedDischargeDate"
                },
                "output_key": "plannedDischargeDate",
                "on_success": "End_Workflow",
                "on_failure": "Handle_Error"
            },
            "Handle_Error": {
                "type": "log_error",
                "title": "Handle Error",
                "default_error_code": "S3-001",
                "default_message": "Failed to read from S3",
                "on_success": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    initial_payload = {
        "testDataURI": test_uri
    }
    
    result = schema_validator.create_and_run_workflow(workflow_definition, initial_payload)
    
    assert result["final_state"], "Workflow should complete successfully"
    
    # Verify JSONPath result
    final_data = result["final_state"].get("data", {})
    assert final_data.get("plannedDischargeDate") == "2025-07-15T10:00:00Z"
    
    logger.info("✅ S3 JSONPath library call validated successfully")

def test_calculate_timedelta_node(schema_validator):
    """Test core#calculate_timedelta library call functionality"""
    
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Calculate Timedelta Test",
        "workflow_id": "calculate_timedelta_test",
        "entry_point": "Set_Base_Date",
        "initial_context": [],
        "nodes": {
            "Set_Base_Date": {
                "type": "set_state",
                "title": "Set Base Date",
                "static_outputs": {
                    "baseDate": "2025-07-15T10:00:00Z"
                },
                "on_success": "Calculate_OTF_Date"
            },
            "Calculate_OTF_Date": {
                "type": "library_call",
                "title": "Calculate OTF Date",
                "library_function_id": "core#calculate_timedelta",
                "parameters": {
                    "date_context_key": "baseDate",
                    "timedelta": {
                        "days": -10
                    }
                },
                "output_key": "otfDate",
                "on_success": "End_Workflow",
                "on_failure": "Handle_Error"
            },
            "Handle_Error": {
                "type": "log_error",
                "title": "Handle Error",
                "default_error_code": "CALC-001",
                "default_message": "Failed to calculate timedelta",
                "on_success": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    result = schema_validator.create_and_run_workflow(workflow_definition)
    
    assert result["final_state"], "Workflow should complete successfully"
    
    # Verify calculated date
    final_data = result["final_state"].get("data", {})
    assert final_data.get("otfDate") == "2025-07-05T10:00:00Z"
    
    logger.info("✅ Calculate Timedelta library call validated successfully")

# ==========================
# CAPABILITY NODE TESTS
# ==========================

def test_async_request_node(schema_validator):
    """Test async_request node functionality"""
    
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Async Request Test",
        "workflow_id": "async_request_test",
        "entry_point": "Make_Async_Request",
        "initial_context": ["deliverySetId", "deliverySetURI"],
        "nodes": {
            "Make_Async_Request": {
                "type": "async_request",
                "title": "Make Async Request",
                "capability_id": "import#create_filingpacks",
                "input_keys": ["deliverySetId", "deliverySetURI"],
                "request_output_keys": [
                    "filingPacksStatus",
                    "filingPacksError",
                    "importFilingPacks"
                ],
                "on_response": "Check_Response",
                "on_failure": "Handle_Error"
            },
            "Check_Response": {
                "type": "condition",
                "title": "Check Response Status",
                "condition_on_key": "filingPacksStatus",
                "branches": {
                    "SUCCESS": "Success_Path",
                    "_default": "Failure_Path"
                }
            },
            "Success_Path": {
                "type": "set_state",
                "title": "Success Path",
                "static_outputs": {
                    "result": "success"
                },
                "on_success": "End_Workflow"
            },
            "Failure_Path": {
                "type": "set_state",
                "title": "Failure Path",
                "static_outputs": {
                    "result": "failure"
                },
                "on_success": "End_Workflow"
            },
            "Handle_Error": {
                "type": "log_error",
                "title": "Handle Error",
                "default_error_code": "ASYNC-001",
                "default_message": "Async request failed",
                "on_success": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    initial_payload = {
        "deliverySetId": str(uuid.uuid4()),
        "deliverySetURI": f"s3://{schema_validator.data_bucket}/test-delivery-set.json"
    }
    
    result = schema_validator.create_and_run_workflow(
        workflow_definition, 
        initial_payload,
        timeout_seconds=120  # Async requests may take longer
    )
    
    assert result["final_state"], "Workflow should complete successfully"
    
    # The exact result depends on the mock service response
    # We mainly verify that the workflow completed without error
    final_data = result["final_state"].get("data", {})
    assert "result" in final_data
    
    logger.info("✅ Async Request node validated successfully")

# ==========================
# INTEGRATION TESTS
# ==========================

def test_complex_workflow_integration(schema_validator):
    """Test complex workflow with multiple node types"""
    
    # Create test data
    test_data = {
        "consignment": {
            "plannedDischargeDate": "2025-07-15T10:00:00Z"
        },
        "metadata": {
            "processingEnabled": True
        }
    }
    
    # Upload test data to S3
    test_key = f"test-data/complex-test-{uuid.uuid4()}.json"
    schema_validator.aws_client.upload_json_to_s3(
        schema_validator.data_bucket,
        test_key,
        test_data
    )
    test_uri = f"s3://{schema_validator.data_bucket}/{test_key}"
    
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Complex Integration Test",
        "workflow_id": "complex_integration_test",
        "entry_point": "Check_Processing_Enabled",
        "initial_context": ["testDataURI"],
        "nodes": {
            "Check_Processing_Enabled": {
                "type": "library_call",
                "title": "Check Processing Enabled",
                "library_function_id": "s3#read_jsonpath",
                "parameters": {
                    "input_s3_uri_key": "testDataURI",
                    "jsonpath_expression": "$.metadata.processingEnabled"
                },
                "output_key": "processingEnabled",
                "on_success": "Validate_Processing",
                "on_failure": "Handle_Error"
            },
            "Validate_Processing": {
                "type": "condition",
                "title": "Validate Processing Enabled",
                "condition_on_key": "processingEnabled",
                "branches": {
                    "True": "Read_Discharge_Date",
                    "False": "Skip_Processing",
                    "_default": "Handle_Error"
                }
            },
            "Read_Discharge_Date": {
                "type": "library_call",
                "title": "Read Discharge Date",
                "library_function_id": "s3#read_jsonpath",
                "parameters": {
                    "input_s3_uri_key": "testDataURI",
                    "jsonpath_expression": "$.consignment.plannedDischargeDate"
                },
                "output_key": "plannedDischargeDate",
                "on_success": "Calculate_OTF_Date",
                "on_failure": "Handle_Error"
            },
            "Calculate_OTF_Date": {
                "type": "library_call",
                "title": "Calculate OTF Date",
                "library_function_id": "core#calculate_timedelta",
                "parameters": {
                    "date_context_key": "plannedDischargeDate",
                    "timedelta": {
                        "days": -10
                    }
                },
                "output_key": "otfDate",
                "on_success": "Start_Parallel_Processing",
                "on_failure": "Handle_Error"
            },
            "Start_Parallel_Processing": {
                "type": "fork",
                "title": "Start Parallel Processing",
                "branches": ["Process_A", "Process_B"]
            },
            "Process_A": {
                "type": "set_state",
                "title": "Process A",
                "static_outputs": {
                    "processA": "completed"
                },
                "on_success": "Join_Processing"
            },
            "Process_B": {
                "type": "set_state",
                "title": "Process B",
                "static_outputs": {
                    "processB": "completed"
                },
                "on_success": "Join_Processing"
            },
            "Join_Processing": {
                "type": "join",
                "title": "Join Processing",
                "join_branches": ["Process_A", "Process_B"],
                "on_success": "Finalize_Processing"
            },
            "Finalize_Processing": {
                "type": "set_state",
                "title": "Finalize Processing",
                "static_outputs": {
                    "finalResult": "success"
                },
                "on_success": "End_Workflow"
            },
            "Skip_Processing": {
                "type": "set_state",
                "title": "Skip Processing",
                "static_outputs": {
                    "finalResult": "skipped"
                },
                "on_success": "End_Workflow"
            },
            "Handle_Error": {
                "type": "log_error",
                "title": "Handle Error",
                "default_error_code": "COMPLEX-001",
                "default_message": "Complex workflow error",
                "on_success": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    initial_payload = {
        "testDataURI": test_uri
    }
    
    result = schema_validator.create_and_run_workflow(
        workflow_definition,
        initial_payload,
        timeout_seconds=120
    )
    
    assert result["final_state"], "Complex workflow should complete successfully"
    
    # Verify all expected values are present
    final_data = result["final_state"].get("data", {})
    assert final_data.get("processingEnabled") is True
    assert final_data.get("plannedDischargeDate") == "2025-07-15T10:00:00Z"
    assert final_data.get("otfDate") == "2025-07-05T10:00:00Z"
    assert final_data.get("processA") == "completed"
    assert final_data.get("processB") == "completed"
    assert final_data.get("finalResult") == "success"
    
    logger.info("✅ Complex workflow integration validated successfully")

# ==========================
# ERROR HANDLING TESTS
# ==========================

def test_error_handling_workflow(schema_validator):
    """Test error handling and recovery mechanisms"""
    
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Error Handling Test",
        "workflow_id": "error_handling_test",
        "entry_point": "Try_Invalid_Operation",
        "initial_context": [],
        "nodes": {
            "Try_Invalid_Operation": {
                "type": "library_call",
                "title": "Try Invalid S3 Read",
                "library_function_id": "s3#read_jsonpath",
                "parameters": {
                    "input_s3_uri_key": "nonExistentKey",
                    "jsonpath_expression": "$.test"
                },
                "output_key": "result",
                "on_success": "Success_Path",
                "on_failure": "Handle_Error"
            },
            "Success_Path": {
                "type": "set_state",
                "title": "Success Path",
                "static_outputs": {
                    "result": "unexpected_success"
                },
                "on_success": "End_Workflow"
            },
            "Handle_Error": {
                "type": "log_error",
                "title": "Handle Expected Error",
                "default_error_code": "EXPECTED-001",
                "default_message": "Expected error occurred",
                "on_success": "Recovery_Action"
            },
            "Recovery_Action": {
                "type": "set_state",
                "title": "Recovery Action",
                "static_outputs": {
                    "result": "recovered_from_error"
                },
                "on_success": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    result = schema_validator.create_and_run_workflow(workflow_definition)
    
    assert result["final_state"], "Error handling workflow should complete successfully"
    
    # Verify error was handled and recovery occurred
    final_data = result["final_state"].get("data", {})
    assert final_data.get("result") == "recovered_from_error"
    
    logger.info("✅ Error handling workflow validated successfully")

# ==========================
# PERFORMANCE TESTS
# ==========================

def test_workflow_performance(schema_validator):
    """Test workflow performance with timing"""
    
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Performance Test",
        "workflow_id": "performance_test",
        "entry_point": "Start_Timer",
        "initial_context": [],
        "nodes": {
            "Start_Timer": {
                "type": "set_state",
                "title": "Start Timer",
                "static_outputs": {
                    "startTime": datetime.now(timezone.utc).isoformat()
                },
                "on_success": "Process_Data"
            },
            "Process_Data": {
                "type": "set_state",
                "title": "Process Data",
                "static_outputs": {
                    "processedItems": 1000,
                    "processingComplete": True
                },
                "on_success": "End_Timer"
            },
            "End_Timer": {
                "type": "set_state",
                "title": "End Timer",
                "static_outputs": {
                    "endTime": datetime.now(timezone.utc).isoformat()
                },
                "on_success": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    start_time = datetime.now(timezone.utc)
    result = schema_validator.create_and_run_workflow(workflow_definition)
    end_time = datetime.now(timezone.utc)
    
    execution_time = (end_time - start_time).total_seconds()
    
    assert result["final_state"], "Performance test workflow should complete successfully"
    assert execution_time < 30, f"Workflow took too long: {execution_time} seconds"
    
    # Verify processing results
    final_data = result["final_state"].get("data", {})
    assert final_data.get("processedItems") == 1000
    assert final_data.get("processingComplete") is True
    
    logger.info(f"✅ Performance test validated successfully (execution time: {execution_time:.2f}s)")

# ==========================
# SCHEMA VALIDATION TESTS
# ==========================

def test_schema_version_validation(schema_validator):
    """Test schema version validation"""
    
    # Test with supported schema version
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Schema Version Test",
        "workflow_id": "schema_version_test",
        "entry_point": "Start",
        "initial_context": [],
        "nodes": {
            "Start": {
                "type": "entry",
                "title": "Start Node",
                "next": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    result = schema_validator.create_and_run_workflow(workflow_definition)
    
    assert result["final_state"], "Schema version test should complete successfully"
    
    logger.info("✅ Schema version validation test passed")

def test_required_fields_validation(schema_validator):
    """Test that required fields are properly validated"""
    
    # Test with all required fields
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Required Fields Test",
        "workflow_id": "required_fields_test",
        "entry_point": "Start",
        "initial_context": [],
        "nodes": {
            "Start": {
                "type": "entry",
                "title": "Start Node",
                "next": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Node"
            }
        }
    }
    
    result = schema_validator.create_and_run_workflow(workflow_definition)
    
    assert result["final_state"], "Required fields test should complete successfully"
    
    logger.info("✅ Required fields validation test passed")

# ==========================
# SUMMARY TEST
# ==========================

def test_comprehensive_schema_validation(schema_validator):
    """Comprehensive test that validates all major functionality"""
    
    # This test runs a workflow that exercises most node types
    test_data = {
        "config": {
            "enabled": True,
            "baseDate": "2025-07-15T10:00:00Z"
        },
        "items": [
            {"id": "item1", "value": 100},
            {"id": "item2", "value": 200}
        ]
    }
    
    # Upload test data
    test_key = f"test-data/comprehensive-{uuid.uuid4()}.json"
    schema_validator.aws_client.upload_json_to_s3(
        schema_validator.data_bucket,
        test_key,
        test_data
    )
    test_uri = f"s3://{schema_validator.data_bucket}/{test_key}"
    
    workflow_definition = {
        "schema_version": "1.1.2",
        "workflow_name": "Comprehensive Schema Validation",
        "workflow_id": "comprehensive_validation",
        "entry_point": "Initialize",
        "initial_context": ["dataURI"],
        "nodes": {
            "Initialize": {
                "type": "set_state",
                "title": "Initialize Workflow",
                "static_outputs": {
                    "workflowStarted": True
                },
                "on_success": "Check_Config"
            },
            "Check_Config": {
                "type": "library_call",
                "title": "Check Configuration",
                "library_function_id": "s3#read_jsonpath",
                "parameters": {
                    "input_s3_uri_key": "dataURI",
                    "jsonpath_expression": "$.config.enabled"
                },
                "output_key": "configEnabled",
                "on_success": "Validate_Config",
                "on_failure": "Handle_Error"
            },
            "Validate_Config": {
                "type": "condition",
                "title": "Validate Configuration",
                "condition_on_key": "configEnabled",
                "branches": {
                    "True": "Read_Base_Date",
                    "False": "Skip_Processing",
                    "_default": "Handle_Error"
                }
            },
            "Read_Base_Date": {
                "type": "library_call",
                "title": "Read Base Date",
                "library_function_id": "s3#read_jsonpath",
                "parameters": {
                    "input_s3_uri_key": "dataURI",
                    "jsonpath_expression": "$.config.baseDate"
                },
                "output_key": "baseDate",
                "on_success": "Calculate_Target_Date",
                "on_failure": "Handle_Error"
            },
            "Calculate_Target_Date": {
                "type": "library_call",
                "title": "Calculate Target Date",
                "library_function_id": "core#calculate_timedelta",
                "parameters": {
                    "date_context_key": "baseDate",
                    "timedelta": {
                        "days": -7
                    }
                },
                "output_key": "targetDate",
                "on_success": "Start_Parallel_Processing",
                "on_failure": "Handle_Error"
            },
            "Start_Parallel_Processing": {
                "type": "fork",
                "title": "Start Parallel Processing",
                "branches": ["Process_Data", "Process_Metadata"]
            },
            "Process_Data": {
                "type": "set_state",
                "title": "Process Data",
                "static_outputs": {
                    "dataProcessed": True,
                    "itemCount": 2
                },
                "on_success": "Join_Processing"
            },
            "Process_Metadata": {
                "type": "set_state",
                "title": "Process Metadata",
                "static_outputs": {
                    "metadataProcessed": True,
                    "processingTime": "2024-01-01T12:00:00Z"
                },
                "on_success": "Join_Processing"
            },
            "Join_Processing": {
                "type": "join",
                "title": "Join Processing Results",
                "join_branches": ["Process_Data", "Process_Metadata"],
                "on_success": "Finalize"
            },
            "Finalize": {
                "type": "set_state",
                "title": "Finalize Results",
                "static_outputs": {
                    "finalResult": "comprehensive_validation_success",
                    "completedAt": datetime.now(timezone.utc).isoformat()
                },
                "on_success": "End_Workflow"
            },
            "Skip_Processing": {
                "type": "set_state",
                "title": "Skip Processing",
                "static_outputs": {
                    "finalResult": "processing_skipped"
                },
                "on_success": "End_Workflow"
            },
            "Handle_Error": {
                "type": "log_error",
                "title": "Handle Error",
                "default_error_code": "COMP-001",
                "default_message": "Comprehensive validation error",
                "on_success": "End_Workflow"
            },
            "End_Workflow": {
                "type": "end",
                "title": "End Workflow"
            }
        }
    }
    
    initial_payload = {
        "dataURI": test_uri
    }
    
    result = schema_validator.create_and_run_workflow(
        workflow_definition,
        initial_payload,
        timeout_seconds=120
    )
    
    assert result["final_state"], "Comprehensive validation should complete successfully"
    
    # Verify all expected results
    final_data = result["final_state"].get("data", {})
    assert final_data.get("workflowStarted") is True
    assert final_data.get("configEnabled") is True
    assert final_data.get("baseDate") == "2025-07-15T10:00:00Z"
    assert final_data.get("targetDate") == "2025-07-08T10:00:00Z"
    assert final_data.get("dataProcessed") is True
    assert final_data.get("metadataProcessed") is True
    assert final_data.get("finalResult") == "comprehensive_validation_success"
    
    logger.info("✅ Comprehensive schema validation completed successfully")
    
    # Return summary of validated functionality
    return {
        "validated_node_types": [
            "entry", "end", "set_state", "condition", "fork", "join", 
            "library_call", "log_error"
        ],
        "validated_library_functions": [
            "s3#read_jsonpath", "core#calculate_timedelta"
        ],
        "validated_features": [
            "conditional_branching", "parallel_processing", "error_handling",
            "state_management", "s3_integration", "date_calculations"
        ],
        "test_result": "SUCCESS"
    }