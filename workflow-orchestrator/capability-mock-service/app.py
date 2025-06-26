import json
import os
import logging
import uuid
from datetime import datetime
from flask import Flask, request, jsonify
from mangum import Mangum
import boto3

# --- Logging ---
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# --- AWS Clients ---
sqs_client = boto3.client('sqs')
dynamodb = boto3.resource('dynamodb')

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
    # Note: A full table scan and batch delete is expensive. For testing, it's ok.
    # A better approach for high-volume use would be to have a per-test-run partition key.
    if not mock_config_table:
        return jsonify({"error": "Mock config table not initialized"}), 500
    try:
        scan = mock_config_table.scan()
        with mock_config_table.batch_writer() as batch:
            for each in scan['Items']:
                batch.delete_item(Key={'capability_id': each['capability_id']})
        msg = "All mock configurations have been reset."
        logger.info(msg)
        return jsonify({"message": msg})
    except Exception as e:
        logger.error(f"Error in /control/reset: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# --- Mangum Handler for Flask ---
mangum_handler = Mangum(app)

# --- Main Lambda Handler ---
def handler(event, context):
    """
    Main Lambda handler that routes events based on their source.
    - API Gateway events are routed to the Flask app (via Mangum).
    - SQS events are processed to simulate capability responses.
    """
    if 'requestContext' in event and 'http' in event['requestContext']:
        # It's an HTTP event from API Gateway
        logger.info("Routing event to HTTP handler (Mangum/Flask)")
        return mangum_handler(event, context)
    
    if 'Records' in event and event['Records'][0]['eventSource'] == 'aws:sqs':
        # It's an SQS event
        logger.info("Routing event to SQS handler")
        return handle_sqs_event(event, context)

    logger.warning(f"Unknown event type received: {event}")
    return {"statusCode": 400, "body": "Unknown event type"}

# --- SQS Event Processing Logic ---
def handle_sqs_event(event, context):
    """
    Processes SQS messages from the orchestrator.
    """
    if not ORCHESTRATOR_COMMAND_QUEUE_URL or not mock_config_table:
        logger.error("Required environment variables for SQS handling are not set.")
        return
        
    for record in event['Records']:
        try:
            message_body = json.loads(record['body'])
            command = message_body.get('command', {})
            payload = command.get('payload', {})
            capability_id = payload.get('capability_id')
            
            if not capability_id:
                logger.error("Missing 'capability_id' in SQS message payload.")
                continue

            logger.info(f"Processing SQS request for capability '{capability_id}'")

            # Get mock configuration from DynamoDB
            response_config = mock_config_table.get_item(Key={'capability_id': capability_id}).get('Item')
            
            if not response_config:
                response_payload = {"error": "No mock configured for this capability"}
            else:
                response_payload = response_config.get("response_data", {})

            # Construct the response command to send back to the orchestrator
            response_message = {
                "workflowInstanceId": message_body.get("workflowInstanceId"),
                "correlationId": message_body.get("correlationId"),
                "workflowDefinitionURI": message_body.get("workflowDefinitionURI"),
                "command": {
                    "type": "ASYNC_RESP",
                    "id": f"resp-cmd-mock-{uuid.uuid4()}",
                    "source": f"Capability:{capability_id}",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "payload": response_payload
                }
            }

            logger.info(f"Sending response to '{ORCHESTRATOR_COMMAND_QUEUE_URL}': {json.dumps(response_message)}")
            sqs_client.send_message(
                QueueUrl=ORCHESTRATOR_COMMAND_QUEUE_URL,
                MessageBody=json.dumps(response_message)
            )

        except Exception as e:
            logger.error(f"Error processing SQS record: {e}", exc_info=True)
            continue
    return {"statusCode": 200, "body": "SQS records processed."}
