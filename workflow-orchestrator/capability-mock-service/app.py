import json
import os
import boto3
import logging
import sys
from datetime import datetime
import uuid

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

sqs_client = boto3.client('sqs')

def handler(event, context):
    """
    Handles SQS events from capability request queues, simulates processing,
    and sends a response back to the orchestrator's reply queue.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    for record in event['Records']:
        try:
            message_body = json.loads(record['body'])
            header = message_body.get('header', {})
            
            reply_to_queue_url = header.get('replyToQueueUrl')
            correlation_id = header.get('correlationId') # This is the workflowInstanceId
            # The capability must receive and return the definition URI to enable stateless resume
            workflow_definition_uri = header.get('workflowDefinitionURI')
            
            capability_id = message_body.get('body', {}).get('capability_id', 'unknown_capability')
            
            # Get the workflowInstanceId from the original request context to pass it back
            workflow_instance_id = message_body.get('body', {}).get('context', {}).get('workflowInstanceId')
            # Get the workflow_id from the original request context to pass it back
            workflow_id = message_body.get('body', {}).get('context', {}).get('workflow_id')

            if not all([reply_to_queue_url, correlation_id, workflow_id, workflow_definition_uri, workflow_instance_id]):
                logger.error("Missing 'replyToQueueUrl', 'correlationId', 'workflow_id', 'workflowDefinitionURI', or 'workflowInstanceId' in the message.")
                continue

            logger.info(f"Processing request for capability '{capability_id}' with correlationId '{correlation_id}' and workflowInstanceId '{workflow_instance_id}'")

            # --- Mock Logic ---
            # Simulate a successful response based on the capability ID
            response_payload = {}
            context = message_body.get('body', {}).get('context', {})
            # Only use asnUri from the context
            asn_uri = context.get('asnUri')
            logger.info(f"asnUri for enrichment: {asn_uri}")
            delivery_sample_uri = None
            if asn_uri and asn_uri.startswith('s3://'):
                import os
                bucket_and_path = asn_uri[5:]
                bucket, *path_parts = bucket_and_path.split('/')
                if path_parts:
                    path_parts[-1] = 'delivery-sample.json'
                    delivery_sample_uri = f"s3://{bucket}/{'/'.join(path_parts)}"
                else:
                    delivery_sample_uri = f"s3://{bucket}/delivery-sample.json"
            logger.info(f"Derived delivery_sample_uri: {delivery_sample_uri}")
            if 'import' in capability_id:
                response_payload = {
                    # Required output key for workflow compatibility
                    "import_enrichment_uri": delivery_sample_uri or "mock-import-uri-value",
                    "us_import_requirements": {
                        "hs_codes_verified": True,
                        "valuation_method": "Method 1",
                        "required_documents": ["C88", "EORI_Auth"]
                    }
                }
            elif 'export' in capability_id:
                response_payload = {
                    "be_export_requirements": {
                        "export_license_needed": False,
                        "eori_number": "BE123456789"
                    }
                }
            else:
                 response_payload = {"status": "completed", "mock_data": "some_default_data"}


            # --- Construct Generic Command Response ---
            request_id = getattr(context, 'aws_request_id', None)
            # Generate a UUID for the command id if not present
            command_id = f"resp-cmd-mock-{request_id}" if request_id else str(uuid.uuid4())
            response_message = {
                "workflowInstanceId": workflow_instance_id,
                "correlationId": header.get("correlationId"),
                "workflowDefinitionURI": workflow_definition_uri,
                "command": {
                    "type": "ASYNC_RESP",
                    "id": command_id,
                    "source": f"Capability:{capability_id}",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "payload": response_payload
                }
            }

            logger.info(f"Sending response to '{reply_to_queue_url}': {json.dumps(response_message)}")

            # Send the response back to the reply queue
            sqs_client.send_message(
                QueueUrl=reply_to_queue_url,
                MessageBody=json.dumps(response_message)
            )

        except Exception as e:
            logger.error(f"Error processing record: {e}")
            # In a real scenario, you might send to a DLQ or handle retries
            continue
            
    return {
        'statusCode': 200,
        'body': json.dumps('Successfully processed records.')
    }
