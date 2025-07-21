import boto3
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add the project root to the path to allow importing from tests.utils
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from tests.utils.cdk_outputs_parser import CdkOutputsParser
from tests.utils.aws_client import AWSClient

class InteractiveTestRunner:
    def __init__(self, scenario_path: Path):
        self.scenario_path = scenario_path
        self.cdk_outputs = self._load_cdk_outputs()

        # Extract required values from CDK outputs
        self.command_queue_url = self.cdk_outputs['OrchestratorCommandQueueUrl']
        self.fallback_capability_queue_url = self.cdk_outputs['FallbackCapabilityQueueUrl']
        self.definitions_bucket = self.cdk_outputs['DefinitionsBucketName']
        self.ingest_bucket = self.cdk_outputs['IngestBucketName']
        role_arn = self.cdk_outputs['TestExecutorRoleArn']
        
        # Detect configured capability queues from environment
        self.configured_capabilities = self._detect_configured_capabilities()

        # Dynamically determine the region from the queue URL
        match = re.search(r'sqs\.([a-z0-9-]+)\.amazonaws\.com', self.command_queue_url)
        if not match:
            raise ValueError(f"Could not determine AWS region from queue URL: {self.command_queue_url}")
        region = match.group(1)

        # Initialize the robust AWS client that assumes the correct role
        self.aws_client = AWSClient(region_name=region, test_executor_role_arn=role_arn)

        print("\n--- Using the following AWS resources ---")
        print(f"  Command Queue URL: {self.command_queue_url}")
        print(f"  Fallback Queue URL: {self.fallback_capability_queue_url}")
        if self.configured_capabilities:
            print(f"  Configured Capabilities: {', '.join(self.configured_capabilities)}")
        else:
            print(f"  No specific capability queues configured - using fallback for all capabilities")
        print(f"  Definitions Bucket: {self.definitions_bucket}")
        print(f"  Ingest Bucket:      {self.ingest_bucket}")
        print("---------------------------------------\n")

    def _load_cdk_outputs(self) -> dict:
        """Loads the CDK outputs using the robust CdkOutputsParser."""
        try:
            parser = CdkOutputsParser(cdk_dir="workflow-orchestrator/cdk")
            all_outputs = parser.get_all_outputs()
            if not all_outputs:
                raise ValueError("CDK outputs are empty.")
            # Assume only one stack's outputs are in the file
            stack_name = list(all_outputs.keys())[0]
            return all_outputs[stack_name]
        except (FileNotFoundError, ValueError, IndexError) as e:
            print(f"Error loading CDK outputs: {e}")
            print("Please ensure the CDK stack has been deployed successfully.")
            exit(1)

    def _detect_configured_capabilities(self) -> list:
        """Detects configured capability queues from environment variables."""
        import os
        capabilities = []
        for key, value in os.environ.items():
            if key.startswith('CCH_CAPABILITY_') and value:
                # Extract capability name (e.g., CCH_CAPABILITY_IMPORT -> IMPORT)
                capability_name = key.replace('CCH_CAPABILITY_', '')
                capabilities.append(capability_name)
        return sorted(capabilities)

    def _upload_file_to_s3(self, bucket: str, file_path: Path, key: str):
        """Uploads a local file to the specified S3 bucket."""
        print(f"Uploading {file_path.name} to s3://{bucket}/{key}...")
        self.aws_client.upload_to_s3(str(file_path), bucket, key)
        return f"s3://{bucket}/{key}"

    def _send_sqs_message(self, queue_url: str, message_body: dict):
        """Sends a message to the specified SQS queue."""
        queue_name = queue_url.split('/')[-1]
        print(f"Sending message to {queue_name}...")
        # The AWSClient handles the JSON serialization, so we pass the dictionary directly.
        self.aws_client.send_sqs_message(
            queue_url=queue_url,
            message_body=message_body
        )

    def _receive_message(self, queue_url: str, wait_time_seconds: int = 20) -> dict | None:
        """Receives a single message from the specified SQS queue."""
        print(f"Waiting for message on {queue_url.split('/')[-1]}...")
        messages = self.aws_client.get_sqs_messages(
            queue_url=queue_url,
            max_messages=1,
            visibility_timeout=wait_time_seconds
        )
        if messages:
            message = messages[0]
            # The message is not explicitly deleted. The visibility timeout handles it.
            # AWSClient already parses the JSON, so message['Body'] is already a dict
            return message['Body']
        return None

    def run(self):
        """Executes the interactive test scenario."""
        print("--- Starting Interactive Workflow Scenario ---")
        correlation_id = f"interactive-test-{uuid.uuid4()}"

        # 1. Upload workflow definition and consignment data
        # Find the workflow definition file in the scenario directory (assumes one YAML file)
        try:
            workflow_def_file = next(self.scenario_path.glob('*.yaml'))
        except StopIteration:
            print("Error: No workflow definition (.yaml file) found in the scenario directory.")
            exit(1)

        workflow_def_uri = self._upload_file_to_s3(
            self.definitions_bucket,
            workflow_def_file,
            f"workflows/{workflow_def_file.name}"
        )
        consignment_uri = self._upload_file_to_s3(
            self.ingest_bucket,
            self.scenario_path / 'consignment.json',
            f"data/consignment-{correlation_id}.json"
        )

        # 2. Send the start_workflow command with the correct nested structure
        command_message = {
            "workflowInstanceId": correlation_id,
            "correlationId": correlation_id,
            "workflowDefinitionURI": workflow_def_uri,
            "command": {
                "type": "EVENT",
                "id": str(uuid.uuid4()),
                "source": "Interactive-HITLErrorHandling",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "consignmentURI": consignment_uri,
                    "_no_cache": True
                }
            }
        }
        self._send_sqs_message(self.command_queue_url, command_message)

        # 3. Interactive loop
        step = 1
        node_call_counts = {}
        last_paused_node = None

        while True:
            action = input(f"\n--- Step {step}: Press Enter to check for capability message, or type 'q' to quit ---")
            if action.lower() == 'q':
                break

            # Always use the fallback queue for testing since it receives all capability requests
            # when no specific capability queues are configured
            capability_message = self._receive_message(self.fallback_capability_queue_url)

            if capability_message:
                # A capability is requesting an action
                print("\n--- Received Capability Request ---")
                print(json.dumps(capability_message, indent=2))
                print("-----------------------------------")

                node_name = capability_message.get('context', {}).get('current_node')
                last_paused_node = node_name # This is the node we expect a response for

                # Increment call count for this node
                call_count = node_call_counts.get(node_name, 0) + 1
                node_call_counts[node_name] = call_count

                # Find the corresponding response file, trying numbered versions first
                response_file = self.scenario_path / f"{node_name}_{call_count}.json"
                if not response_file.exists():
                    response_file = self.scenario_path / f"{node_name}.json"

                if not response_file.exists():
                    print(f"ERROR: No response file found for node '{node_name}' (attempt {call_count}). Cannot continue.")
                    break

                with open(response_file, 'r') as f:
                    response_payload = json.load(f)

                response_payload['correlationId'] = capability_message.get('correlationId')

                print(f"\nFound response file: {response_file.name}")
                input("--- Press Enter to send the response back to the orchestrator ---")
                self._send_sqs_message(self.command_queue_url, response_payload)
                print("Response sent.")

            else:
                # No message received, likely paused at an event_wait node
                if not last_paused_node:
                    print("Workflow finished. No capability messages and no prior paused node.")
                    break

                print(f"\nWorkflow appears to be paused, likely at node '{last_paused_node}'.")
                # Look for a file named after the paused node to send as a resolving command
                resume_file = self.scenario_path / f"{last_paused_node}.json"
                if not resume_file.exists():
                    print(f"No resume file found for paused node '{last_paused_node}'. The workflow may be complete or stuck.")
                    break
                
                print(f"Found resume command file: {resume_file.name}")
                input("--- Press Enter to send the resume command ---")

                with open(resume_file, 'r') as f:
                    resume_payload = json.load(f)
                
                # Inject the correlationId into the resume command
                resume_payload['correlationId'] = correlation_id
                resume_payload['workflowInstanceId'] = correlation_id

                self._send_sqs_message(self.command_queue_url, resume_payload)
                print("Resume command sent.")
                last_paused_node = None # Reset, as we've sent the resume command

            step += 1

        print("\n--- Scenario Finished ---")

if __name__ == "__main__":
    # The scenario is located in the same directory as the script
    current_scenario_path = Path(__file__).parent
    runner = InteractiveTestRunner(current_scenario_path)
    runner.run()
