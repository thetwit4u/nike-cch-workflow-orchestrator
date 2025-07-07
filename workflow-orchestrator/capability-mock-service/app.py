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
            logger.info(f"Received message body: {json.dumps(message_body, indent=2)}") # Added for debugging

            # Correctly parse the nested command structure from the orchestrator
            command = message_body.get('command', {})
            # The orchestrator sends the main data in 'body', not 'payload'
            command_body = command.get('body', {}) 
            capability_id = command_body.get('capability_id')
            context = command_body.get('context', {})

            if not capability_id:
                logger.error("Missing 'capability_id' in command body.")
                continue

            logger.info(f"Processing SQS request for capability '{capability_id}'")

            # Get mock configuration from DynamoDB
            response_config = mock_config_table.get_item(Key={'capability_id': capability_id}).get('Item')
            
            response_payload = {}
            if response_config:
                # Use the configured response if it exists
                response_payload = response_config.get("response_data", {})
                logger.info(f"Found configured mock for '{capability_id}'.")
            else:
                # --- ADDED: Default logic for unconfigured capabilities ---
                logger.info(f"No mock configured for '{capability_id}'. Using default logic.")
                if capability_id == "import#enrich_deliveryset":
                    response_payload = {
                        "deliverySetImportEnrichedId": context.get("deliverySetId", str(uuid.uuid4())),
                        "deliverySetImportEnrichedURI": context.get("deliverySetURI", "").replace(".json", "-enriched.json"),
                        "deliverySetImportEnrichedStatus": "ENRICHED",
                        "deliverySetImportEnrichedError": None
                    }
                elif capability_id == "import#create_filingpacks":
                    # This is the merged capability for the simplified workflow.
                    # It needs to return both enrichment and filing pack data.
                    response_payload = {
                        "deliverySetImportEnrichedId": context.get("deliverySetId", str(uuid.uuid4())),
                        "deliverySetImportEnrichedURI": context.get("deliverySetURI", "").replace(".json", "-enriched.json"),
                        "deliverySetImportEnrichedStatus": "SUCCESS",
                        "importFilingPacksStatus": "SUCCESS",
                        "importFilingPacksError": None,
                        "importFilingPacks": [
                            {"filingPackId": str(uuid.uuid4()), "status": "PENDING"},
                            {"filingPackId": str(uuid.uuid4()), "status": "PENDING"}
                        ]
                    }
                else:
                    response_payload = {"error": "No mock configured for this capability"}


            # Construct the response message to send back to the orchestrator
            response_command = {
                "workflowInstanceId": message_body.get("workflowInstanceId"),
                "correlationId": message_body.get("correlationId"),
                "workflowDefinitionURI": message_body.get("workflowDefinitionURI"),
                "command": {
                    "type": "ASYNC_RESP",
                    "id": f"resp-cmd-mock-{uuid.uuid4()}",
                    "source": f"Capability:{capability_id}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": response_payload,
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
