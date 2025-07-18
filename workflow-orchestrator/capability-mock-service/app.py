import json
import os
import logging
import uuid
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import boto3
import botocore
import awsgi

# --- Logging ---
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# Log the application version to confirm deployment
APP_VERSION = os.environ.get('VERSION', 'unknown')
logger.info(f"--- Mock Service starting up, Version: {APP_VERSION} ---")

# --- AWS Clients ---
sqs_client = boto3.client('sqs')
dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3') # Add S3 client

# --- Environment Variables ---
MOCK_CONFIG_TABLE_NAME = os.environ.get('MOCK_CONFIG_TABLE_NAME')
ORCHESTRATOR_COMMAND_QUEUE_URL = os.environ.get('ORCHESTRATOR_COMMAND_QUEUE_URL')

# Initialize table resource only if the table name is provided
mock_config_table = None
if MOCK_CONFIG_TABLE_NAME:
    mock_config_table = dynamodb.Table(MOCK_CONFIG_TABLE_NAME)

# --- Flask App for HTTP Control Plane---
app = Flask(__name__)

@app.route('/control/configure', methods=['POST'])
def configure_mock():
    if not mock_config_table:
        return jsonify({"error": "Mock config table not initialized"}), 500
    try:
        data = request.json
        capability_id = data.get('capability')
        if not capability_id:
            return jsonify({"error": "'capability' field is required"}), 400
        
        config = {
            "capability_id": capability_id,
            "response_type": data.get("response_type", "SUCCESS"),
            "response_data": data.get("response_data", {})
        }
        mock_config_table.put_item(Item=config)
        
        msg = f"Successfully configured mock for capability: {capability_id}"
        logger.info(msg)
        return jsonify({"message": msg, "configuration": config})
    except Exception as e:
        logger.error(f"Error in /control/configure: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/control/reset', methods=['POST'])
def reset_mocks():
    logger.info("Received request to reset all mock configurations.")
    if not mock_config_table:
        logger.error("Cannot reset: Mock config table not initialized.")
        return jsonify({"error": "Mock config table not initialized"}), 500
    try:
        logger.info(f"Scanning table: {mock_config_table.table_name}")
        scan = mock_config_table.scan()
        items = scan.get('Items', [])
        logger.info(f"Found {len(items)} items to delete.")
        
        if not items:
            return jsonify({"message": "No mock configurations to reset."})

        with mock_config_table.batch_writer() as batch:
            for each in items:
                batch.delete_item(Key={'capability_id': each['capability_id']})
        
        msg = "All mock configurations have been reset."
        logger.info(msg)
        return jsonify({"message": msg})
    except botocore.exceptions.ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        error_message = e.response.get("Error", {}).get("Message")
        logger.error(f"AWS ClientError on reset: {error_code} - {error_message}", exc_info=True)
        return jsonify({
            "error": "AWSClientError",
            "code": error_code,
            "message": error_message
        }), 500
    except Exception as e:
        logger.error(f"An unexpected error occurred during reset: {e}", exc_info=True)
        return jsonify({"error": "UnexpectedError", "message": str(e)}), 500

# --- SQS Event Processing Logic ---
def handle_sqs_event(event, context_aws):
    """
    Processes SQS messages from the orchestrator.
    """
    if not ORCHESTRATOR_COMMAND_QUEUE_URL:
        logger.error("ORCHESTRATOR_COMMAND_QUEUE_URL environment variable is not set.")
        return

    for record in event['Records']:
        try:
            message_body = json.loads(record['body'])
            logger.info(f"Received message body: {json.dumps(message_body, indent=2)}")

            capability_id = message_body.get('command', {}).get('capability_id')
            context = message_body.get('command', {}).get('context', {})

            if not capability_id:
                logger.error("Missing 'capability_id' in command.")
                continue

            logger.info(f"Processing SQS request for capability '{capability_id}'")

            response_payload = {}
            response_status = "SUCCESS"

            # On retry, the 'resumed_from_hitl' flag is present. Always succeed.
            if context.get("resumed_from_hitl"):
                logger.info("'resumed_from_hitl' is True. This is a retry. Returning SUCCESS.")
                response_status = "SUCCESS"
                response_payload = {
                    "consignmentImportEnrichedId": context.get("consignmentId", str(uuid.uuid4())),
                    "consignmentImportEnrichedURI": context.get("consignmentURI", "").replace(".json", "-enriched.json"),
                    "importFilingPacks": [
                        {"filingPackId": str(uuid.uuid4()), "status": "PENDING"},
                        {"filingPackId": str(uuid.uuid4()), "status": "PENDING"}
                    ]
                }

            # On initial run, the test script sends 'test_hitl_error' to force the error path.
            elif context.get("test_hitl_error"):
                logger.info("'test_hitl_error' is True. Returning HITL error response.")
                response_status = "ERROR"
                response_payload = {
                    "messages": [{
                        "messageId": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(),
                        "level": "ERROR", "code": "HITL_REQUIRED",
                        "summary": "A recoverable error was triggered for testing.",
                        "context": {"capabilityId": capability_id}
                    }]
                }
            # Default case is success (for happy path tests).
            else:
                logger.info("No error flags detected. Returning SUCCESS for happy path.")
                response_status = "SUCCESS"
                response_payload = {
                    "consignmentImportEnrichedId": context.get("consignmentId", str(uuid.uuid4())),
                    "consignmentImportEnrichedURI": context.get("consignmentURI", "").replace(".json", "-enriched.json"),
                    "importFilingPacks": [
                        {"filingPackId": str(uuid.uuid4()), "status": "PENDING"},
                        {"filingPackId": str(uuid.uuid4()), "status": "PENDING"}
                    ]
                }

            response_command = {
                "workflowInstanceId": message_body.get("workflowInstanceId"),
                "correlationId": message_body.get("correlationId"),
                "workflowDefinitionURI": message_body.get("workflowDefinitionURI"),
                "command": {
                    "type": "ASYNC_RESP",
                    "id": f"resp-cmd-mock-{uuid.uuid4()}",
                    "source": f"Capability:{capability_id}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": response_status,
                    "payload": response_payload
                }
            }

            logger.info(f"Sending response to '{ORCHESTRATOR_COMMAND_QUEUE_URL}': {json.dumps(response_command)}")
            sqs_client.send_message(
                QueueUrl=ORCHESTRATOR_COMMAND_QUEUE_URL,
                MessageBody=json.dumps(response_command)
            )

        except Exception as e:
            logger.error(f"Error processing SQS record: {e}", exc_info=True)
            continue
    return {"statusCode": 200, "body": "SQS records processed."}


# --- Main Lambda Handler ---
def handler(event, context):
    """
    Main Lambda handler that routes events based on their source.
    - API Gateway events are routed to the Flask app (via awsgi).
    - SQS events are processed to simulate capability responses.
    """
    # Check for an API Gateway event and route to Flask
    if 'httpMethod' in event:
        return awsgi.response(app, event, context)
    
    # Check for an SQS event and process accordingly
    if 'Records' in event and event['Records'][0]['eventSource'] == 'aws:sqs':
        logger.info("Routing event to SQS handler")
        return handle_sqs_event(event, context)

    logger.warning(f"Unknown event type received: {event}")
    return {"statusCode": 400, "body": "Unknown event type"}
