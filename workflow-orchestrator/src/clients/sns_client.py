import boto3
import json
import logging
from typing import Union, Dict
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

class SnsClient:
    """
    A client for interacting with Amazon SNS.
    """
    def __init__(self):
        self.client = boto3.client('sns')

    def publish_message(self, topic_arn: str, message_body: Union[Dict, str], message_group_id: str):
        """
        Publishes a message to an SNS topic.

        :param topic_arn: The ARN of the target SNS topic.
        :param message_body: A dictionary representing the message body, which will be serialized to JSON.
        :param message_group_id: The ID for the message group.
        """
        if isinstance(message_body, dict):
            body_str = json.dumps(message_body)
        elif isinstance(message_body, str):
            body_str = message_body
        else:
            raise TypeError(f"message_body must be a dict or a str, not {type(message_body).__name__}")

        try:
            logger.info(f"Publishing message to topic: {topic_arn}")
            self.client.publish(
                TopicArn=topic_arn,
                Message=body_str,
                MessageGroupId=message_group_id
            )
            logger.info("Message published successfully.")
        except ClientError as e:
            logger.error(f"Error publishing message to SNS topic {topic_arn}: {e}")
            raise
