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
from tests.utils.workflow_verifier import WorkflowVerifier

# Try to import pygments for syntax highlighting
try:
    from pygments import highlight
    from pygments.lexers import JsonLexer
    from pygments.formatters import TerminalFormatter
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False

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
        
        # Initialize workflow verifier for checking final states
        workflow_state_table = self.cdk_outputs['WorkflowStateTableName']
        self.workflow_verifier = WorkflowVerifier(self.aws_client, workflow_state_table)

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

    def _format_json(self, data: dict) -> str:
        """Format JSON with syntax highlighting if pygments is available."""
        json_str = json.dumps(data, indent=2)
        if PYGMENTS_AVAILABLE:
            return highlight(json_str, JsonLexer(), TerminalFormatter())
        return json_str

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

    def _receive_message(self, queue_url: str, wait_time_seconds: int = 20) -> tuple[dict, str] | None:
        """Receives a single message from the specified SQS queue.
        Returns tuple of (message_body, receipt_handle) or None.
        """
        print(f"Waiting for message on {queue_url.split('/')[-1]}...")
        messages = self.aws_client.get_sqs_messages(
            queue_url=queue_url,
            max_messages=1,
            visibility_timeout=wait_time_seconds
        )
        if messages:
            message = messages[0]
            # The body is a JSON string, so we need to parse it.
            body = json.loads(message['Body'])
            # Return both the parsed message body and the receipt handle for deletion
            return body, message['ReceiptHandle']
        return None

    def run(self):
        """Executes the interactive test scenario."""
        print("--- Starting Interactive Workflow Scenario ---")
        if not PYGMENTS_AVAILABLE:
            print("💡 Tip: Install 'pygments' for colorized JSON output: pip install pygments")
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
            # Use shorter timeout (10s) for better interactive experience
            result = self._receive_message(self.fallback_capability_queue_url, wait_time_seconds=10)

            if result:
                capability_message, receipt_handle = result
                # Store the receipt handle so we can delete the message after processing
                self._current_receipt_handle = receipt_handle
                # A capability is requesting an action
                print("\n--- Received Capability Request ---")
                print(self._format_json(capability_message))
                print("-----------------------------------")

                # Map capability_id to node name for finding response files
                capability_id = capability_message.get('body', {}).get('capability_id', '')
                # Map capability ID to node name (e.g., "import#create_filingpacks" -> "Create_Filing_Packs")
                capability_to_node = {
                    'import#create_filingpacks': 'Create_Filing_Packs',
                    'import#enrich_consignment': 'Enrich_Consignment',
                    # Add more mappings as needed
                }
                node_name = capability_to_node.get(capability_id, capability_id.replace('#', '_').replace('_', '_').title())
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

                # The original_request is the message from the queue, with a header and body.
                original_header = capability_message.get('header', {})

                # Create a proper response message structure
                response_message = {
                    "workflowInstanceId": original_header.get('workflowInstanceId'),
                    "correlationId": original_header.get('correlationId'),
                    "workflowDefinitionURI": original_header.get('workflowDefinitionURI'),
                    "command": {
                        "type": "ASYNC_RESP",
                        "id": str(uuid.uuid4()),
                        "source": "MockCapabilityService",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "in_reply_to": original_header.get('commandId'),
                        "status": "SUCCESS" if response_payload.get('status') == 'SUCCESS' else 'ERROR',
                        "payload": response_payload.get('data', {})
                    }
                }

                # Add routing hint if it was in the original request's header
                if 'routingHint' in original_header:
                    response_message['command']['routingHint'] = original_header['routingHint']

                print(f"\nFound response file: {response_file.name}")
                print("Response payload:")
                print(self._format_json(response_message))
                input("--- Press Enter to send the response back to the orchestrator ---")
                self._send_sqs_message(self.command_queue_url, response_message)
                print("Response sent.")
                
                # Delete the processed capability message from the queue
                self._delete_processed_message()
                
                # If we sent an ERROR response, immediately handle HITL resolution
                if response_payload.get('status') == 'ERROR':
                    last_paused_node = 'Wait_Enrich_Exception_Resolution'
                    print(f"Sent ERROR response - workflow will pause at '{last_paused_node}'")
                    
                    # Give the workflow a moment to process the error and pause
                    import time
                    print("Waiting for workflow to pause...")
                    time.sleep(2)
                    
                    # Now send the HITL resolution immediately
                    resume_file = self.scenario_path / f"{last_paused_node}.json"
                    if resume_file.exists():
                        print(f"\nSending HITL resolution for '{last_paused_node}'")
                        
                        with open(resume_file, 'r') as f:
                            resume_payload = json.load(f)
                        
                        # Populate placeholder values
                        resume_payload['correlationId'] = correlation_id
                        resume_payload['workflowInstanceId'] = correlation_id
                        
                        if 'workflowDefinitionURI' in resume_payload and resume_payload['workflowDefinitionURI'] == 'PLACEHOLDER_WORKFLOW_DEFINITION_URI':
                            workflow_def_file = next(self.scenario_path.glob('*.yaml'))
                            resume_payload['workflowDefinitionURI'] = f"s3://{self.definitions_bucket}/workflows/{workflow_def_file.name}"
                        
                        if 'command' in resume_payload:
                            command = resume_payload['command']
                            if command.get('id') == 'PLACEHOLDER_COMMAND_ID':
                                command['id'] = str(uuid.uuid4())
                            if command.get('timestamp') == 'PLACEHOLDER_TIMESTAMP':
                                command['timestamp'] = datetime.now(timezone.utc).isoformat()

                        print("HITL Resolution payload:")
                        print(self._format_json(resume_payload))
                        input("--- Press Enter to send the HITL resolution ---")
                        self._send_sqs_message(self.command_queue_url, resume_payload)
                        print("HITL resolution sent.")
                        
                        # Give the workflow time to process HITL resolution and retry the capability
                        print("Waiting for workflow to process HITL resolution and retry capability...")
                        time.sleep(5)  # Longer wait for retry capability request
                        
                        # Reset for next capability request (retry)
                        last_paused_node = None
                    else:
                        print(f"ERROR: No HITL resolution file found: {resume_file}")
                else:
                    last_paused_node = None  # Reset for success responses

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
                
                # Populate all placeholder values in the resume command
                resume_payload['correlationId'] = correlation_id
                resume_payload['workflowInstanceId'] = correlation_id
                
                # Get workflow definition URI from the first capability message if available
                if 'workflowDefinitionURI' in resume_payload and resume_payload['workflowDefinitionURI'] == 'PLACEHOLDER_WORKFLOW_DEFINITION_URI':
                    # Try to get from previous capability message or construct it
                    workflow_def_file = next(self.scenario_path.glob('*.yaml'))
                    resume_payload['workflowDefinitionURI'] = f"s3://{self.definitions_bucket}/workflows/{workflow_def_file.name}"
                
                # Populate command-level placeholders
                if 'command' in resume_payload:
                    command = resume_payload['command']
                    if command.get('id') == 'PLACEHOLDER_COMMAND_ID':
                        command['id'] = str(uuid.uuid4())
                    if command.get('timestamp') == 'PLACEHOLDER_TIMESTAMP':
                        command['timestamp'] = datetime.now(timezone.utc).isoformat()

                print("Resume command payload:")
                print(self._format_json(resume_payload))
                self._send_sqs_message(self.command_queue_url, resume_payload)
                print("Resume command sent.")
                last_paused_node = None # Reset, as we've sent the resume command

            step += 1

        # 4. Verify final workflow state
        print("\n--- Verifying Final Workflow State ---")
        self._verify_workflow_completion(correlation_id)
        
        print("\n--- Scenario Finished ---")

    def _delete_processed_message(self):
        """Delete the currently processed capability message from the queue."""
        if hasattr(self, '_current_receipt_handle') and self._current_receipt_handle:
            print("Deleting processed message from queue...")
            self.aws_client.delete_sqs_message(
                self.fallback_capability_queue_url, 
                self._current_receipt_handle
            )
            self._current_receipt_handle = None
            print("Message deleted successfully.")
        else:
            print("No message to delete (receipt handle not found).")

    def _verify_workflow_completion(self, correlation_id: str):
        """Verify that the workflow has completed successfully."""
        try:
            # Poll for final state with a reasonable timeout
            final_state = self.workflow_verifier.poll_for_final_state(
                correlation_id,
                lambda state: (
                    state.get("data", {}).get("status") == "COMPLETED" or
                    state.get("context", {}).get("current_node") == "End_Workflow"
                ),
                timeout_seconds=60,
                interval_seconds=5
            )
            
            if final_state:
                context = final_state.get("context", {})
                data = final_state.get("data", {})
                current_node = context.get("current_node")
                status = data.get("status")
                
                print(f"✅ Workflow completed successfully!")
                print(f"   Final Node: {current_node}")
                print(f"   Status: {status}")
                
                # Show any messages that were processed during HITL
                messages = data.get("messages", [])
                if messages:
                    print(f"   Messages processed: {len(messages)}")
                    for msg in messages:
                        print(f"     - {msg.get('level', 'INFO')}: {msg.get('summary', 'No summary')}")
            else:
                print("❌ Workflow did not complete within the expected timeframe.")
                
        except Exception as e:
            print(f"❌ Error verifying workflow completion: {e}")

if __name__ == "__main__":
    # The scenario is located in the same directory as the script
    current_scenario_path = Path(__file__).parent
    runner = InteractiveTestRunner(current_scenario_path)
    runner.run()
