import pytest
from pytest_bdd import given, when, then, parsers, scenario
import logging

from tests.utils.env_manager import TestEnvironmentManager
from tests.utils.cdk_outputs_parser import CdkOutputsParser
from tests.utils.mock_service_client import MockServiceClient
from tests.utils.test_data_loader import TestDataLoader
from tests.utils.aws_client import AWSClient
from tests.utils.workflow_verifier import WorkflowVerifier

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Fixtures ---

@pytest.fixture(scope="session")
def test_environment_manager():
    """Manages the lifecycle of the test environment."""
    manager = TestEnvironmentManager()
    try:
        manager.deploy()
        yield manager
    finally:
        manager.destroy()

@pytest.fixture(scope="session")
def cdk_outputs(test_environment_manager):
    """Parses and provides CDK outputs."""
    return CdkOutputsParser()

@pytest.fixture(scope="session")
def stack_name(cdk_outputs):
    """Dynamically determines the stack name from the CDK outputs."""
    outputs = cdk_outputs.get_all_outputs()
    # Assuming there is only one stack deployed for the test
    if len(outputs) != 1:
        raise ValueError(f"Expected 1 stack in cdk-outputs.json, but found {len(outputs)}")
    return list(outputs.keys())[0]
    
@pytest.fixture(scope="session")
def aws_client():
    """Provides a client for interacting with AWS services."""
    return AWSClient()

@pytest.fixture(scope="session")
def mock_service_client(cdk_outputs, stack_name):
    """Provides a client for the mock capability service."""
    # Assuming the mock service API Gateway URL is an output
    mock_api_url = cdk_outputs.get_output(stack_name, "MockServiceApiEndpoint")
    return MockServiceClient(base_url=mock_api_url)

@pytest.fixture(scope="session")
def workflow_verifier(aws_client, cdk_outputs, stack_name):
    """Provides a utility to verify workflow state in DynamoDB."""
    table_name = cdk_outputs.get_output(stack_name, "CheckpointTableName")
    return WorkflowVerifier(aws_client, table_name)

@pytest.fixture(scope="function")
def test_data_loader():
    """Provides a utility to load test data files."""
    return TestDataLoader()

@pytest.fixture(scope="function")
def scenario_context():
    """A dictionary to share state between steps in a single scenario."""
    return {}

# --- Background Steps ---

@given("the CCH test environment is deployed successfully")
def check_environment(test_environment_manager):
    """
    This step is handled by the `test_environment_manager` fixture.
    The fixture's setup ensures the environment is deployed before any tests run.
    If the deployment fails, the test session will abort.
    """
    logger.info("CCH Test Environment is deployed and ready.")
    pass

@given("the mock services are configured for the upcoming scenario")
def reset_mocks(mock_service_client):
    """
    Resets all mock service configurations to a clean state before each scenario.
    """
    logger.info("Resetting all mock configurations.")
    mock_service_client.reset_all() 