import boto3
import json
import logging
from typing import Union, Dict
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

class QueueClient:
    """
    A client for interacting with Amazon SQS.
    """
    def __init__(self):
        self.client = boto3.client('sqs')

    def send_message(self, queue_url: str, message_body: Union[Dict, str], message_attributes: Dict = None):
        """
        Sends a message to an SQS queue.

        :param queue_url: The URL of the target SQS queue.
        :param message_body: A dictionary representing the message body, which will be serialized to JSON.
        :param message_attributes: Additional attributes for the message.
        """
        if not message_attributes:
            message_attributes = {}

        # If the message body is a dictionary, serialize it to JSON.
        # If it's already a string, use it as is.
        if isinstance(message_body, dict):
            body_str = json.dumps(message_body)
        elif isinstance(message_body, str):
            body_str = message_body
        else:
            raise TypeError(f"message_body must be a dict or a str, not {type(message_body).__name__}")

        try:
            logger.info(f"Sending message to queue: {queue_url}")
            self.client.send_message(
                QueueUrl=queue_url,
                MessageBody=body_str,
                MessageAttributes=message_attributes
            )
            logger.info("Message sent successfully.")
        except ClientError as e:
            logger.error(f"Error sending message to SQS queue {queue_url}: {e}")
            raise
