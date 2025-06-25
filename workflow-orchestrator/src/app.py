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
command_parser = CommandParser()

def handler(event, context):
    """
    Main Lambda entry point.
    Processes SQS records by preparing and then invoking the appropriate workflow graph.
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
            
            # 1. Validate the incoming command
            if not command_parser.parse_and_validate(message_body):
                logger.error(f"Invalid command message structure. Skipping record: {record['messageId']}")
                continue

            # 2. Prepare the graph and inputs for execution
            graph, inputs, config = orchestrator_service.prepare_graph_for_execution(message_body)
            
            if not graph:
                logger.warning(f"Could not prepare graph for message. Skipping record: {record['messageId']}")
                continue

            # 3. Invoke the graph
            # This is a synchronous call for now. LangGraph's async invoke (`ainvoke`)
            # is used for concurrent runs, not for running a single graph asynchronously
            # within an async handler. The execution itself is handled by LangGraph.
            graph.invoke(inputs, config)
            logger.info(f"Successfully invoked graph for workflow instance: {config['configurable']['thread_id']}")

        except Exception:
            logger.exception(f"Error processing SQS record: {record.get('messageId', 'N/A')}")
            # For now, we'll just log and continue to the next record.
            # A DLQ should be configured on the SQS queue for production.
            continue
            
    return {
        'statusCode': 200,
        'body': json.dumps('Successfully processed records.')
    } 