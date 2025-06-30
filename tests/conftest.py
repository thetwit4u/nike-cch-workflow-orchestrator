import pytest
from tests.utils.workflow_verifier import WorkflowVerifier
from tests.utils.aws_client import AWSClient
from tests.utils.cdk_outputs_parser import CdkOutputsParser
import os
import logging
import re

# This file is intentionally left empty for now.
# Its presence helps pytest discover modules and plugins correctly for the tests
# within this directory. Pytest automatically handles the path, so manual
# sys.path manipulation is not needed and can cause discovery conflicts.

logger = logging.getLogger(__name__)

@pytest.fixture(scope="session")
def cdk_outputs():
    # The parser is initialized with the relative path from the project root
    parser = CdkOutputsParser(cdk_dir="workflow-orchestrator/cdk")
    return parser

@pytest.fixture(scope="session")
def stack_name(cdk_outputs):
    """
    Dynamically determines the stack name from the cdk-outputs.json file.
    This avoids hardcoding environment-specific names like '-dev-bdd'.
    """
    all_outputs = cdk_outputs.get_all_outputs()
    # Assuming there is only one top-level stack key in the outputs file
    if not all_outputs:
        raise ValueError("CDK outputs are empty. Cannot determine stack name.")
    
    stack_keys = list(all_outputs.keys())
    logger.info(f"Dynamically determined stack name: {stack_keys[0]}")
    return stack_keys[0]

@pytest.fixture(scope="session")
def aws_client(cdk_outputs, stack_name):
    """Initializes the AWSClient with role assumption for tests."""
    
    # Retrieve role ARN from CDK outputs, ensuring it exists.
    role_arn = cdk_outputs.get_output(stack_name, "TestExecutorRoleArn")
    if not role_arn:
        raise ValueError("TestExecutorRoleArn not found in CDK outputs. Deploy the CDK stack.")
    
    # Dynamically determine the region from a regionalized output, like the queue URL
    queue_url = cdk_outputs.get_output(stack_name, "OrchestratorCommandQueueUrl")
    match = re.search(r'sqs\.([a-z0-9-]+)\.amazonaws\.com', queue_url)
    if not match:
        raise ValueError(f"Could not determine AWS region from queue URL: {queue_url}")
    
    region = match.group(1)
    logger.info(f"Dynamically determined AWS region: {region}")

    # Initialize the client with the correct region and role
    client = AWSClient(region_name=region, test_executor_role_arn=role_arn)
    return client

@pytest.fixture(scope="module")
def workflow_verifier(aws_client, cdk_outputs, stack_name):
    """Provides a verifier instance for checking workflow state."""
    table_name = cdk_outputs.get_output(stack_name, "CheckpointTableName")
    if not table_name:
        raise ValueError("CheckpointTableName not found in CDK outputs. Deploy the CDK stack.")
        
    return WorkflowVerifier(aws_client, table_name) 