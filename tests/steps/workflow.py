from pytest_bdd import scenario, given, when, then, parsers
import logging

# Make fixtures available in this module
from .environment import *

logger = logging.getLogger(__name__)

# --- Re-usable steps for different scenarios ---

@scenario('../features/import_us_workflow.feature', 'Happy Path - Successful workflow execution from start to finish')
def test_happy_path():
    """Defines the scenario from the feature file that this test function runs."""
    pass

@given(parsers.parse('a valid "DeliverySet" file "{filename}" is available in S3'))
def store_filename(scenario_context, test_data_loader, filename):
    """
    Loads the test data and stores the filename and content in the scenario context.
    The actual upload will happen in the 'When' step.
    """
    logger.info(f"Loading data for DeliverySet file: {filename}")
    scenario_context['delivery_set_filename'] = filename
    scenario_context['delivery_set_content'] = test_data_loader.load_json(filename)
    # Extract thread_id for later use, assuming it's based on deliverySetId
    scenario_context['thread_id'] = scenario_context['delivery_set_content']['deliverySetId']


@given(parsers.parse('the mock "{capability}" service is configured to return "{status}"'))
def configure_mock_success(mock_service_client, capability, status):
    """Configures a mock service to return a success response."""
    # This is a simplified example. We might need to add response_data for real cases.
    mock_service_client.configure_response(
        capability=capability,
        response_type="SUCCESS",
        response_data={"status": status}
    )

@then(parsers.parse('a new workflow with ID "{workflow_id}" should be successfully initiated'))
def check_workflow_initiation(workflow_verifier, scenario_context, workflow_id):
    """

    Verifies that the workflow has been initiated and has a valid state in DynamoDB.
    We check for the existence of the item, assuming initiation means a record is created.
    """
    thread_id = scenario_context.get('thread_id')
    assert thread_id, "thread_id not found in scenario_context"
    
    # A simple assertion to check if the record exists
    def assertion(state):
        # We just need the state to exist to confirm initiation.
        return state is not None

    assert workflow_verifier.poll_for_state_update(thread_id, assertion, timeout_seconds=15), \
        f"Workflow with thread_id '{thread_id}' did not initiate within 15 seconds."

@then(parsers.parse('the workflow should eventually reach the "end" state with status "{status}"'))
def check_workflow_completion(workflow_verifier, scenario_context, status):
    """
    Verifies that the workflow reaches the 'end' node and has a final status of 'COMPLETED'.
    """
    thread_id = scenario_context.get('thread_id')
    assert thread_id, "thread_id not found in scenario_context"

    # We need to check both the final node and the overall status in the context.
    def assertion(state):
        context = state.get('context', {})
        current_node = context.get('current_node')
        workflow_status = context.get('status')
        logger.info(f"Polling: current_node='{current_node}', workflow_status='{workflow_status}'")
        return current_node == 'end' and workflow_status == status

    assert workflow_verifier.poll_for_state_update(thread_id, assertion, timeout_seconds=60), \
        f"Workflow did not complete with status '{status}' within 60 seconds." 