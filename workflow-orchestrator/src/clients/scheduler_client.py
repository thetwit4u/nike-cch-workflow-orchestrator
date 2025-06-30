import boto3
from botocore.exceptions import ClientError
import logging
import json
import os
import hashlib
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

class SchedulerClient:
    """A client for managing EventBridge Scheduler operations."""

    def __init__(self):
        self.client = boto3.client("scheduler")
        self.role_arn = os.environ.get("SCHEDULER_ROLE_ARN")
        self.group_name = os.environ.get("SCHEDULER_GROUP_NAME")
        if not self.role_arn:
            raise ValueError("SCHEDULER_ROLE_ARN environment variable not set.")
        if not self.group_name:
            raise ValueError("SCHEDULER_GROUP_NAME environment variable not set.")

    def create_or_update_onetime_schedule(self, schedule_name: str, schedule_time: str, target_arn: str, payload: dict, state_context: dict = None):
        """
        Creates or updates a one-time schedule that sends a message to an SQS queue.
        If a schedule with the same name exists, it will be updated.

        :param schedule_name: A unique name for the schedule. This will be hashed to fit constraints.
        :param schedule_time: The UTC time for the schedule to run, in 'YYYY-MM-DDTHH:MM:SS' format.
        :param target_arn: The ARN of the target SQS queue.
        :param payload: The JSON payload to send to the target (the internal command).
        :param state_context: The full workflow state, used to check for test overrides.
        """
        # Hash the schedule name to ensure it's within the 64-character limit
        hashed_name = hashlib.sha1(schedule_name.encode()).hexdigest()
        short_name = f"cch-{hashed_name[:56]}" # Prefix and truncate to be safe

        # --- Test Override Logic ---
        # Check if a test override is present in the workflow's data context
        if state_context:
            data_context = state_context.get("data", {})
            override_seconds = data_context.get("test_schedule_override_seconds")

            if override_seconds:
                try:
                    # Calculate the new schedule time based on the override
                    now = datetime.now(timezone.utc)
                    future_time = now + timedelta(seconds=int(override_seconds))
                    schedule_time = future_time.strftime('%Y-%m-%dT%H:%M:%S')
                    logger.warning(f"Test override found! Setting schedule '{short_name}' to run in {override_seconds} seconds at {schedule_time}Z.")
                except (ValueError, TypeError):
                    logger.error(f"Invalid value for 'test_schedule_override_seconds': {override_seconds}. Using original time.")
        
        try:
            # EventBridge Scheduler requires the time in a specific format without timezone info (it assumes UTC)
            schedule_expression = f"at({schedule_time})"
            
            logger.info(f"Creating one-time schedule '{short_name}' for {schedule_expression} to target '{target_arn}'.")

            target = {
                "Arn": target_arn,
                "RoleArn": self.role_arn,
                "Input": json.dumps(payload),
            }

            self.client.create_schedule(
                Name=short_name,
                GroupName=self.group_name,
                ScheduleExpression=schedule_expression,
                ScheduleExpressionTimezone="UTC",
                Target=target,
                FlexibleTimeWindow={"Mode": "OFF"},
                ActionAfterCompletion="DELETE", # The schedule deletes itself after running
                State="ENABLED"
            )
            logger.info(f"Successfully created schedule '{short_name}'.")
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConflictException':
                logger.warning(f"Schedule '{short_name}' already exists. Updating it with new time/payload.")
                self.client.update_schedule(
                    Name=short_name,
                    GroupName=self.group_name,
                    ScheduleExpression=schedule_expression,
                    ScheduleExpressionTimezone="UTC",
                    Target=target,
                    FlexibleTimeWindow={"Mode": "OFF"},
                    ActionAfterCompletion="DELETE",
                    State="ENABLED"
                )
                logger.info(f"Successfully updated schedule '{short_name}'.")
            else:
                logger.error(f"Error creating/updating schedule '{short_name}': {e}")
                raise

    def delete_schedule(self, schedule_name: str):
        """Deletes a schedule."""
        # Hash the name to find the correct schedule to delete
        hashed_name = hashlib.sha1(schedule_name.encode()).hexdigest()
        short_name = f"cch-{hashed_name[:56]}"
        try:
            logger.info(f"Deleting schedule '{short_name}'.")
            self.client.delete_schedule(Name=short_name, GroupName=self.group_name)
            logger.info(f"Successfully deleted schedule '{short_name}'.")
        except ClientError as e:
            # It's okay if the schedule is already gone
            if e.response['Error']['Code'] != 'ResourceNotFoundException':
                logger.error(f"Error deleting schedule '{short_name}': {e}")
                raise 