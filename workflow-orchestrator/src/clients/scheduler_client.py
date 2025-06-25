import boto3
from botocore.exceptions import ClientError
import logging
import json
import os

logger = logging.getLogger(__name__)

class SchedulerClient:
    """A client for managing EventBridge Scheduler operations."""

    def __init__(self):
        self.client = boto3.client("scheduler")
        self.role_arn = os.environ.get("SCHEDULER_ROLE_ARN")
        if not self.role_arn:
            raise ValueError("SCHEDULER_ROLE_ARN environment variable not set.")

    def create_or_update_onetime_schedule(self, schedule_name: str, schedule_time: str, target_arn: str, payload: dict):
        """
        Creates or updates a one-time schedule that sends a message to an SQS queue.
        If a schedule with the same name exists, it will be updated.

        :param schedule_name: A unique name for the schedule.
        :param schedule_time: The UTC time for the schedule to run, in 'YYYY-MM-DDTHH:MM:SS' format.
        :param target_arn: The ARN of the target SQS queue.
        :param payload: The JSON payload to send to the target.
        """
        try:
            # EventBridge Scheduler requires the time in a specific format without timezone info (it assumes UTC)
            schedule_expression = f"at({schedule_time})"
            
            logger.info(f"Creating one-time schedule '{schedule_name}' for {schedule_expression} to target '{target_arn}'.")

            target = {
                "Arn": target_arn,
                "RoleArn": self.role_arn,
                "Input": json.dumps(payload),
                "SqsParameters": {
                    "MessageGroupId": payload.get("workflowInstanceId", "default_group")
                }
            }

            self.client.create_schedule(
                Name=schedule_name,
                GroupName="cch-workflow-schedules", # A default group name
                ScheduleExpression=schedule_expression,
                ScheduleExpressionTimezone="UTC",
                Target=target,
                FlexibleTimeWindow={"Mode": "OFF"},
                ActionAfterCompletion="DELETE", # The schedule deletes itself after running
                State="ENABLED"
            )
            logger.info(f"Successfully created schedule '{schedule_name}'.")
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConflictException':
                logger.warning(f"Schedule '{schedule_name}' already exists. Updating it with new time/payload.")
                self.client.update_schedule(
                    Name=schedule_name,
                    GroupName="cch-workflow-schedules",
                    ScheduleExpression=schedule_expression,
                    ScheduleExpressionTimezone="UTC",
                    Target=target,
                    FlexibleTimeWindow={"Mode": "OFF"},
                    ActionAfterCompletion="DELETE",
                    State="ENABLED"
                )
                logger.info(f"Successfully updated schedule '{schedule_name}'.")
            else:
                logger.error(f"Error creating/updating schedule '{schedule_name}': {e}")
                raise

    def delete_schedule(self, schedule_name: str):
        """Deletes a schedule."""
        try:
            logger.info(f"Deleting schedule '{schedule_name}'.")
            self.client.delete_schedule(Name=schedule_name, GroupName="cch-workflow-schedules")
            logger.info(f"Successfully deleted schedule '{schedule_name}'.")
        except ClientError as e:
            # It's okay if the schedule is already gone
            if e.response['Error']['Code'] != 'ResourceNotFoundException':
                logger.error(f"Error deleting schedule '{schedule_name}': {e}")
                raise 