import boto3
import json
import logging

logger = logging.getLogger(__name__)

class QueueClient:
    """
    A client for interacting with Amazon SQS.
    """
    def __init__(self):
        self.client = boto3.client('sqs')

    def send_message(self, queue_url: str, message_body: dict):
        """
        Sends a message to an SQS queue.

        :param queue_url: The URL of the target SQS queue.
        :param message_body: A dictionary representing the message body, which will be serialized to JSON.
        """
        try:
            logger.info(f"Sending message to queue: {queue_url}")
            self.client.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(message_body)
            )
            logger.info("Message sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send message to SQS queue '{queue_url}': {e}")
            raise
