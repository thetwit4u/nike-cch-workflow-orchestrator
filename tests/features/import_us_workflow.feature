Feature: End-to-End Test for the US Import Workflow (import_us_v1)

  As a QA Engineer, I want to test the import_us_v1 workflow
  to ensure it orchestrates all compliance steps correctly
  from start to finish.

  Background:
    Given the CCH test environment is deployed successfully
    And the mock services are configured for the upcoming scenario

  Scenario: Happy Path - Successful workflow execution from start to finish
    Given a valid "DeliverySet" file "test-data-happy-path.json" is available in S3
    And the mock "declaration_communicator#submit_import" service is configured to return "SUBMITTED"
    And the mock "declaration_communicator#customs_released" service is configured to return a success response
    When I upload the "DeliverySet" file to the cch-ingest-s3-bucket
    Then a new workflow with ID "import_us_v1" should be successfully initiated
    And the "s3#read_jsonpath" library function should execute successfully
    And the "core#calculate_timedelta" library function should execute successfully
    And the workflow state should be updated with "filingpackCreationStarted: true"
    And the workflow should wait for multiple events after the fork
    And the workflow should eventually reach the "end" state with status "COMPLETED"
    And a final notification message should have been sent to the "Declaration Communicator"

  Scenario: Pre-existing Workflow - The check for an already started workflow prevents re-execution
    Given a workflow for "deliverySetId-002" has already been initiated and its context contains "filingpackCreationStarted: true"
    And a "DeliverySet" file "test-data-pre-existing.json" is prepared for the same "deliverySetId-002"
    When I upload the "DeliverySet" file to the cch-ingest-s3-bucket
    Then the workflow should transition to the "Log_Error_Already_Started" node
    And the workflow should end immediately without processing

  Scenario: Capability Failure - An async_request to submit a filing pack fails
    Given a valid "DeliverySet" file "test-data-submit-fails.json" is available in S3
    And the mock "declaration_communicator#submit_import" service is configured to return an error status "FAILED"
    When I upload the "DeliverySet" file to the cch-ingest-s3-bucket
    Then the workflow should initiate and create filing packs
    And after receiving the async response, it should transition to the "Check_Submit_Success" node
    And the condition should route the flow to the "Log_Submit_Error" node
    And the workflow should terminate with a logged error

  Scenario: Invalid Input Data - The s3#read_jsonpath library call fails
    Given a "DeliverySet" file "test-data-invalid-input.json" that is missing the required 'plannedDischargeDate' field is in S3
    When I upload the "DeliverySet" file to the cch-ingest-s3-bucket
    Then the "Retrieve_Planned_Discharge_Date" node should fail during the 's3#read_jsonpath' library call
    And the workflow should transition to the "Handle_Error" node
    And the workflow should terminate with a logged error

  Scenario: Conditional Branching - Filing Pack creation fails from the scheduled request
    Given the mock "import#create_filingpacks" capability (triggered by the scheduled_request) is configured to return a "FAILED" status
    And a valid "DeliverySet" file "test-data-creation-fails.json" is prepared
    When I upload the "DeliverySet" file and the scheduled task executes
    Then the "Check_Start_Import_Filing_Result" node should receive the "FAILED" status
    And its condition should route the workflow to the "Import_Filing_Pack_Creation_Error" node
    And the workflow should terminate with a logged error

  Scenario: Library Call - core#calculate_timedelta successfully calculates a new date
    Given the workflow context contains "plannedDischargeDate" with the value "2025-01-15T12:00:00Z"
    And the workflow is at the "Calculate_OTF_Date" node
    When the "Calculate_OTF_Date" node executes
    Then the context should be updated with "currentOtfDate" having the value "2025-01-05T12:00:00Z"
    And the workflow should transition to "Set_Schedule_OTF_Date" 