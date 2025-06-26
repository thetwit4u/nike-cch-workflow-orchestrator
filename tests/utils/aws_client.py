import boto3
import logging
import json

logger = logging.getLogger(__name__)

class AWSClient:
    """
    A Boto3 wrapper for interacting with AWS services required for testing.
    """

    def __init__(self, region_name: str = "us-east-1"):
        """
        Initializes the AWS client.

        Args:
            region_name: The AWS region to connect to.
        """
        self.s3_client = boto3.client("s3", region_name=region_name)
        self.sqs_client = boto3.client("sqs", region_name=region_name)
        self.dynamodb_resource = boto3.resource("dynamodb", region_name=region_name)
        logger.info(f"AWSClient initialized for region {region_name}")

    def upload_to_s3(self, file_path: str, bucket_name: str, object_key: str):
        """
        Uploads a local file to an S3 bucket.

        Args:
            file_path: The local path to the file to upload.
            bucket_name: The name of the target S3 bucket.
            object_key: The key (path) for the object in the bucket.
        """
        logger.info(f"Uploading {file_path} to s3://{bucket_name}/{object_key}")
        try:
            self.s3_client.upload_file(file_path, bucket_name, object_key)
            logger.info("Upload successful.")
        except Exception as e:
            logger.error(f"Failed to upload {file_path} to {bucket_name}: {e}")
            raise

    def get_sqs_messages(self, queue_url: str, max_messages: int = 1, visibility_timeout: int = 5) -> list[dict]:
        """
        Retrieves messages from an SQS queue.

        Args:
            queue_url: The URL of the SQS queue.
            max_messages: The maximum number of messages to retrieve.
            visibility_timeout: The visibility timeout for the retrieved messages.

        Returns:
            A list of messages, with each message body parsed as a dictionary.
        """
        logger.info(f"Retrieving up to {max_messages} messages from {queue_url}")
        try:
            response = self.sqs_client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=max_messages,
                VisibilityTimeout=visibility_timeout,
                WaitTimeSeconds=5 # Use long polling
            )
            messages = response.get("Messages", [])
            logger.info(f"Retrieved {len(messages)} messages.")
            # Parse the message body from JSON string to dict
            for msg in messages:
                if 'Body' in msg:
                    msg['Body'] = json.loads(msg['Body'])
            return messages
        except Exception as e:
            logger.error(f"Failed to retrieve messages from {queue_url}: {e}")
            raise
    
    def get_dynamodb_item(self, table_name: str, key: dict) -> dict:
        """
        Retrieves an item from a DynamoDB table.

        Args:
            table_name: The name of the DynamoDB table.
            key: The primary key of the item to retrieve (e.g., {'thread_id': 'some-id'}).

        Returns:
            The DynamoDB item, or None if not found.
        """
        logger.info(f"Getting item with key {key} from DynamoDB table {table_name}")
        try:
            table = self.dynamodb_resource.Table(table_name)
            response = table.get_item(Key=key)
            item = response.get('Item')
            if item:
                logger.info("Item found.")
            else:
                logger.warning("Item not found.")
            return item
        except Exception as e:
            logger.error(f"Failed to get item from DynamoDB table {table_name}: {e}")
            raise 