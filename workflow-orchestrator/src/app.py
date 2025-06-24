import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import logging
import asyncio

from orchestrator.service import OrchestratorService
from utils.logger import setup_logging

# Configure logging at the entry point
setup_logging()

# Get a logger for this specific module
logger = logging.getLogger(__name__)

# Initialize service and clients
orchestrator_service = OrchestratorService()

def handler(event, context):
    """
    Main Lambda entry point.
    Processes SQS records by delegating to the OrchestratorService.
    This is a synchronous wrapper that runs the async main logic.
    """
    return asyncio.run(main(event, context))

async def main(event, context):
    """
    Asynchronous main logic for the Lambda handler.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    for record in event['Records']:
        try:
            message_body = json.loads(record['body'])
            
            # Delegate to the service
            await orchestrator_service.process_command(message_body)

        except Exception:
            logger.exception("Error processing SQS record:")
            # Depending on the error, you might want to let the message
            # be re-processed or send it to a Dead-Letter Queue (DLQ).
            # For now, we'll just log and continue.
            continue
            
    return {
        'statusCode': 200,
        'body': json.dumps('Successfully processed records.')
    } 