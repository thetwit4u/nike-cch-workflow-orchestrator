import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import logging
import asyncio

from orchestrator.service import OrchestratorService
from utils.logger import setup_logging
from utils.command_parser import CommandParser

# Configure logging at the entry point
setup_logging()

# Get a logger for this specific module
logger = logging.getLogger(__name__)

# Initialize service as a singleton
orchestrator_service = OrchestratorService.get_instance()

def handler(event, context):
    """
    Main Lambda entry point.
    Processes SQS records by passing them to the OrchestratorService.
    """
    # The OrchestratorService's process_command is synchronous,
    # so we don't need an async main loop here anymore.
    logger.info(f"Received event: {json.dumps(event)}")

    for record in event['Records']:
        try:
            message_body = json.loads(record['body'])
            orchestrator_service.process_command(message_body)
            
        except Exception:
            logger.exception(f"Error processing SQS record: {record.get('messageId', 'N/A')}")
            # For now, we'll just log and continue to the next record.
            # A DLQ should be configured on the SQS queue for production.
            continue
            
    return {
        'statusCode': 200,
        'body': json.dumps('Successfully processed records.')
    } 