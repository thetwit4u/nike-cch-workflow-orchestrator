import logging
import os
import pytest

# Configure logging
logger = logging.getLogger(__name__)

def test_upload_workflow_definition(aws_client, cdk_outputs, stack_name):
    """
    Tests the ability to upload a workflow definition file to the designated S3 bucket.
    
    This is a fundamental check to ensure the test environment has basic permissions
    and connectivity to the S3 bucket where workflow definitions are stored.
    """
    # --- ARRANGE ---
    # Get the bucket name from CDK outputs
    definitions_bucket_name = cdk_outputs.get_output(stack_name, "DefinitionsBucketName")
    assert definitions_bucket_name, "DefinitionsBucketName not found in CDK outputs."
    
    # Define the local file path and the target S3 object key
    local_filepath = "workflow-definitions/trivial_workflow.yaml"
    s3_object_key = os.path.basename(local_filepath)

    # Ensure the local file exists before attempting to upload
    assert os.path.exists(local_filepath), f"Local workflow definition file not found at {local_filepath}"
    
    logger.info(f"Attempting to upload {local_filepath} to bucket {definitions_bucket_name} with key {s3_object_key}")

    # --- ACT ---
    # Upload the file to S3
    aws_client.upload_to_s3(
        file_path=local_filepath,
        bucket_name=definitions_bucket_name,
        object_key=s3_object_key
    )

    # --- ASSERT ---
    # Verify the object now exists in the S3 bucket
    logger.info(f"Verifying that object {s3_object_key} exists in bucket {definitions_bucket_name}")
    assert aws_client.object_exists(definitions_bucket_name, s3_object_key), \
        f"File '{s3_object_key}' was not found in bucket '{definitions_bucket_name}' after upload."
    
    logger.info("S3 upload verification successful.") 