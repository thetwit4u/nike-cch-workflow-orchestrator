import boto3
import json
import os
import time
import uuid
from pathlib import Path

class InteractiveTestRunner:
    def __init__(self, scenario_path: Path):
        self.scenario_path = scenario_path
        self.cdk_outputs = self._load_cdk_outputs()
        self.s3_client = boto3.client('s3')
        self.sqs_client = boto3.client('sqs')

        self.command_queue_url = self.cdk_outputs['OrchestratorCommandQueueUrl']
        self.debug_capability_queue_url = self.cdk_outputs['DebugCapabilityQueueUrl']
        self.definitions_bucket = self.cdk_outputs['DefinitionsBucketName']
        self.ingest_bucket = self.cdk_outputs['IngestBucketName']

    def _load_cdk_outputs(self) -> dict:
        """Loads the CDK outputs from the generated JSON file."""
        try:
            outputs_path = Path(__file__).parent.parent.parent / 'workflow-orchestrator' / 'cdk' / 'cdk-outputs.json'
            with open(outputs_path, 'r') as f:
                # The file contains a single key which is the stack name
                data = json.load(f)
                stack_name = list(data.keys())[0]
                return data[stack_name]
        except FileNotFoundError:
            print("Error: cdk-outputs.json not found. Please deploy the CDK stack first.")
            exit(1)

    def _upload_file_to_s3(self, bucket: str, file_path: Path, key: str):
        """Uploads a local file to the specified S3 bucket."""
        print(f"Uploading {file_path.name} to s3://{bucket}/{key}...")
        self.s3_client.upload_file(str(file_path), bucket, key)
        return f"s3://{bucket}/{key}"

    def _send_sqs_message(self, queue_url: str, message_body: dict):
        """Sends a JSON message to the specified SQS queue."""
        print(f"Sending message to {queue_url.split('/')[-1]}...")
        self.sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message_body)
        )

    def _receive_message(self, queue_url: str, wait_time_seconds: int = 20) -> dict | None:
        """Receives a message from the specified SQS queue."""
        print(f"Waiting for message on {queue_url.split('/')[-1]}...")
        response = self.sqs_client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_time_seconds,
            AttributeNames=['All']
        )
        if 'Messages' in response:
            message = response['Messages'][0]
            # Delete the message after receiving it
            self.sqs_client.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=message['ReceiptHandle']
            )
            return json.loads(message['Body'])
        return None

    def run(self):
        """Executes the interactive test scenario."""
        print("--- Starting Interactive Workflow Scenario ---")
        correlation_id = f"interactive-test-{uuid.uuid4()}"

        # 1. Upload workflow definition and consignment data
        workflow_def_uri = self._upload_file_to_s3(
            self.definitions_bucket,
            self.scenario_path / 'import_us_v1.1.1-simplified.yaml',
            f"workflows/import_us_v1.1.1-simplified-{correlation_id}.yaml"
        )
        consignment_uri = self._upload_file_to_s3(
            self.ingest_bucket,
            self.scenario_path / 'consignment.json',
            f"data/consignment-{correlation_id}.json"
        )

        # 2. Send the start_workflow command
        start_command = {
            'command': 'start_workflow',
            'correlationId': correlation_id,
            'workflow_definition_uri': workflow_def_uri,
            'data': {
                'consignmentURI': consignment_uri
            }
        }
        self._send_sqs_message(self.command_queue_url, start_command)

        # 3. Interactive loop
        step = 1
        while True:
            input(f"\n--- Press Enter to check for capability message (Step {step}) ---")
            capability_message = self._receive_message(self.debug_capability_queue_url)

            if not capability_message:
                print("No more messages from the orchestrator. Workflow likely complete.")
                break

            print("\n--- Received Capability Request ---")
            print(json.dumps(capability_message, indent=2))
            print("-----------------------------------")

            # Find the corresponding response message
            node_name = capability_message.get('context', {}).get('current_node')
            response_file = self.scenario_path / f"{node_name}.json"

            if not response_file.exists():
                print(f"ERROR: No response file found for node '{node_name}'. Cannot continue.")
                print(f"Looked for: {response_file}")
                break

            with open(response_file, 'r') as f:
                response_payload = json.load(f)

            # Inject the correct correlationId into the response
            response_payload['correlationId'] = capability_message.get('correlationId')

            print(f"\nFound response file: {response_file.name}")
            input("--- Press Enter to send the response back to the orchestrator ---")
            self._send_sqs_message(self.command_queue_url, response_payload)
            print("Response sent.")
            step += 1

        print("\n--- Scenario Finished ---")

if __name__ == "__main__":
    # The scenario is located in the same directory as the script
    current_scenario_path = Path(__file__).parent
    runner = InteractiveTestRunner(current_scenario_path)
    runner.run()
