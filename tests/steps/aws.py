from pytest_bdd import when, parsers
import logging
import os

# Make fixtures available in this module
from .environment import *

logger = logging.getLogger(__name__)

@when(parsers.parse('I upload the "DeliverySet" file to the cch-ingest-s3-bucket'))
def upload_delivery_set(
    aws_client,
    test_data_loader,
    cdk_outputs,
    stack_name,
    scenario_context
):
    """
    Triggers the workflow by uploading the scenario's DeliverySet JSON file
    to the ingest S3 bucket.
    """
    filename = scenario_context.get('delivery_set_filename')
    assert filename, "delivery_set_filename not found in scenario_context"

    file_path = test_data_loader.get_file_path(filename)
    bucket_name = cdk_outputs.get_output(stack_name, "IngestBucketName")
    
    # The object key should be the filename itself.
    object_key = os.path.basename(file_path)

    logger.info(f"Uploading {file_path} to S3 bucket {bucket_name} with key {object_key}")
    aws_client.upload_to_s3(file_path, bucket_name, object_key) 