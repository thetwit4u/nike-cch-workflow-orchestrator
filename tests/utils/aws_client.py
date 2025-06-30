import boto3
import logging
import json
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

class AWSClient:
    """
    A Boto3 wrapper for interacting with AWS services required for testing.
    """

    def __init__(self, region_name: str, test_executor_role_arn: str = None):
        """
        Initializes the AWSClient, assuming a role if the ARN is provided.
        
        Args:
            region_name: The AWS region to use for the clients.
            test_executor_role_arn: If provided, the client will assume this role.
        """
        self.region = region_name
        self.sts_client = boto3.client("sts")

        creds = self._assume_role(test_executor_role_arn) if test_executor_role_arn else None

        self.s3_client = self._get_client("s3", creds)
        self.sqs_client = self._get_client("sqs", creds)
        self.dynamodb_client = self._get_client("dynamodb", creds)
        
        # Initialize resources, which use the same credential logic
        self.s3_resource = self._get_resource("s3", creds)
        self.dynamodb_resource = self._get_resource("dynamodb", creds)

        logger.info(f"AWSClient initialized for region {self.region}")

    def _get_client(self, service_name: str, creds: dict | None):
        """Creates a boto3 client, using credentials if provided."""
        kwargs = {"region_name": self.region}
        if creds:
            kwargs.update({
                "aws_access_key_id": creds["AccessKeyId"],
                "aws_secret_access_key": creds["SecretAccessKey"],
                "aws_session_token": creds["SessionToken"],
            })
        return boto3.client(service_name, **kwargs)

    def _get_resource(self, service_name: str, creds: dict | None):
        """Creates a boto3 resource, using credentials if provided."""
        kwargs = {"region_name": self.region}
        if creds:
            kwargs.update({
                "aws_access_key_id": creds["AccessKeyId"],
                "aws_secret_access_key": creds["SecretAccessKey"],
                "aws_session_token": creds["SessionToken"],
            })
        return boto3.resource(service_name, **kwargs)

    def _assume_role(self, role_arn: str) -> dict:
        """
        Assumes an IAM role and returns the temporary credentials.

        Args:
            role_arn: The ARN of the IAM role to assume.

        Returns:
            A dictionary containing the temporary credentials.
        """
        logger.info(f"Assuming role: {role_arn}")
        response = self.sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName="BDDTestSession"
        )
        return response['Credentials']

    def bucket_exists(self, bucket_name: str) -> bool:
        """Checks if an S3 bucket exists."""
        try:
            self.s3_client.head_bucket(Bucket=bucket_name)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            else:
                logger.error(f"Error checking for bucket {bucket_name}: {e}")
                raise

    def queue_exists(self, queue_url: str) -> bool:
        """Checks if an SQS queue exists."""
        try:
            self.sqs_client.get_queue_attributes(QueueUrl=queue_url, AttributeNames=['QueueArn'])
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'AWS.SimpleQueueService.NonExistentQueue':
                return False
            else:
                logger.error(f"Error checking for queue {queue_url}: {e}")
                raise

    def table_exists(self, table_name: str) -> bool:
        """Checks if a DynamoDB table exists."""
        try:
            table = self.dynamodb_resource.Table(table_name)
            table.load()  # This will raise a ResourceNotFoundException if the table doesn't exist
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                return False
            else:
                logger.error(f"Error checking for table {table_name}: {e}")
                raise

    def object_exists(self, bucket_name: str, object_key: str) -> bool:
        """Checks if an object exists in an S3 bucket."""
        try:
            self.s3_client.head_object(Bucket=bucket_name, Key=object_key)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            else:
                logger.error(f"Error checking for object s3://{bucket_name}/{object_key}: {e}")
                raise

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
    
    def query_most_recent_checkpoint(self, table_name: str, thread_id: str) -> dict | None:
        """Queries DynamoDB for the most recent checkpoint for a given thread_id."""
        logger.info(f"Querying for most recent checkpoint for thread_id {thread_id} in table {table_name}")
        try:
            table = self.dynamodb_resource.Table(table_name)
            # Query for items with the given PK, and where the SK starts with '#checkpoint#'.
            # This is critical to filter out intermediate #write# entries.
            response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key('PK').eq(thread_id) & boto3.dynamodb.conditions.Key('SK').begins_with('#checkpoint#'),
                ScanIndexForward=False, # Sort descending to get the latest checkpoint
                Limit=1
            )
            items = response.get('Items', [])
            if items:
                logger.info("Most recent checkpoint item found.")
                return items[0]
            else:
                logger.warning("Item not found.")
                return None
        except Exception as e:
            logger.error(f"Failed to query checkpoint from DynamoDB table {table_name}: {e}")
            raise

    def query_all_checkpoints(self, table_name: str, thread_id: str) -> list[dict]:
        """Queries DynamoDB for ALL checkpoints for a given thread_id."""
        logger.info(f"Querying for ALL checkpoints for thread_id {thread_id} in table {table_name}")
        try:
            table = self.dynamodb_resource.Table(table_name)
            response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key('PK').eq(thread_id) & boto3.dynamodb.conditions.Key('SK').begins_with('#checkpoint#'),
                ScanIndexForward=True, # Sort ascending to get the history in order
            )
            items = response.get('Items', [])
            logger.info(f"Found {len(items)} total checkpoint items.")
            return items
        except Exception as e:
            logger.error(f"Failed to query all checkpoints from DynamoDB table {table_name}: {e}")
            raise

    def send_sqs_message(self, queue_url: str, message_body: dict):
        """
        Sends a message to an SQS queue.

        Args:
            queue_url: The URL of the SQS queue.
            message_body: A dictionary representing the message body, which will be converted to JSON.
        """
        logger.info(f"Sending message to SQS queue: {queue_url}")
        try:
            self.sqs_client.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(message_body)
            )
            logger.info("Message sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send message to {queue_url}: {e}")
            raise

    def upload_json_to_s3(self, bucket: str, object_key: str, data: dict):
        """
        Uploads a Python dictionary as a JSON object to an S3 bucket.

        Args:
            bucket: The name of the target S3 bucket.
            object_key: The key for the new object in the bucket.
            data: The Python dictionary to upload.
        """
        try:
            s3_object = self.s3_resource.Object(bucket, object_key)
            s3_object.put(
                Body=json.dumps(data, indent=2),
                ContentType='application/json'
            )
            logger.info(f"Successfully uploaded JSON data to s3://{bucket}/{object_key}")
        except ClientError as e:
            logger.error(f"Failed to upload JSON to s3://{bucket}/{object_key}: {e}")
            raise

    def get_s3_object_body(self, bucket: str, key: str) -> str:
        """
        Retrieves the body of an S3 object as a string.

        Args:
            bucket: The name of the S3 bucket.
            key: The key of the S3 object.

        Returns:
            The body of the S3 object as a string.
        """
        try:
            response = self.s3_client.get_object(Bucket=bucket, Key=key)
            return response['Body'].read().decode('utf-8')
        except ClientError as e:
            logger.error(f"Failed to retrieve object body from s3://{bucket}/{key}: {e}")
            raise 