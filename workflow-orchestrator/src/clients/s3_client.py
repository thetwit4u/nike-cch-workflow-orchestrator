import os
import yaml
import logging
import boto3
import json
from utils.parser import parse_workflow_definition

logger = logging.getLogger(__name__)

class S3Client:
    """A client for interacting with the S3 bucket containing workflow definitions."""

    def __init__(self):
        logger.info("Initializing S3Client...")
        # The bucket name is provided by the Lambda environment variables
        self.bucket_name = os.environ.get("DEFINITIONS_BUCKET_NAME")
        if not self.bucket_name:
            logger.error("DEFINITIONS_BUCKET_NAME environment variable not found or is empty.")
            raise ValueError("DEFINITIONS_BUCKET_NAME environment variable not set.")
        logger.info(f"S3Client initialized with bucket: {self.bucket_name}")
        self.s3 = boto3.client('s3')

    def get_workflow_definition(self, s3_uri: str) -> dict:
        """
        Fetches a workflow definition file from S3 and parses it as YAML.
        """
        try:
            bucket_name, s3_key = self._parse_s3_uri(s3_uri)
            logger.info(f"Fetching workflow definition from bucket '{bucket_name}' with key '{s3_key}'")
            
            response = self.s3.get_object(Bucket=bucket_name, Key=s3_key)
            yaml_content = response['Body'].read().decode('utf-8')
            
            return parse_workflow_definition(yaml_content)
        except Exception as e:
            logger.error(f"Error fetching/parsing definition from {s3_uri}: {e}")
            raise

    def read_json(self, bucket_name: str, s3_key: str) -> dict:
        """
        Fetches an object from S3 and parses it as JSON.
        """
        try:
            logger.info(f"Fetching JSON from bucket '{bucket_name}' with key '{s3_key}'")
            
            response = self.s3.get_object(Bucket=bucket_name, Key=s3_key)
            json_content = response['Body'].read().decode('utf-8')
            
            return json.loads(json_content)
        except Exception as e:
            logger.error(f"Error fetching/parsing JSON from s3://{bucket_name}/{s3_key}: {e}")
            raise

    def _parse_s3_uri(self, s3_uri: str) -> tuple[str, str]:
        """A simple utility to parse an S3 URI into bucket and key."""
        if not s3_uri.startswith("s3://"):
            raise ValueError(f"Invalid S3 URI: {s3_uri}")
        parts = s3_uri[5:].split('/', 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid S3 URI format: {s3_uri}")
        bucket = parts[0]
        key = parts[1]
        return bucket, key

    def get_uri_for_key(self, s3_key: str) -> str:
        """Constructs the S3 URI for a given key."""
        if not hasattr(self, 'bucket_name') or not self.bucket_name:
             logger.error(f"S3Client is missing 'bucket_name' attribute. Object state: {self.__dict__}")
             raise AttributeError("'S3Client' object has no attribute 'bucket_name' or it is empty.")
        return f"s3://{self.bucket_name}/{s3_key}"
