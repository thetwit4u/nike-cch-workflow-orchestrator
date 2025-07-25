import boto3
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    from pygments import highlight
    from pygments.lexers import JsonLexer
    from pygments.formatters import TerminalFormatter
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False

# Add the project root to the path to allow importing from tests.utils
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from tests.utils.cdk_outputs_parser import CdkOutputsParser
from tests.utils.aws_client import AWSClient
from tests.utils.workflow_verifier import WorkflowVerifier

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
        
        # Initialize workflow verifier for state checking
        checkpoint_table_name = self.cdk_outputs['WorkflowStateTableName']
        self.workflow_verifier = WorkflowVerifier(self.aws_client, checkpoint_table_name)

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

    def _format_json(self, data: dict, indent: int = 2) -> str:
        """Format JSON with syntax highlighting if available."""
        json_str = json.dumps(data, indent=indent)
        
        if PYGMENTS_AVAILABLE:
            try:
                return highlight(
                    json_str,
                    JsonLexer(),
                    TerminalFormatter(style='monokai')
                ).rstrip()
            except Exception:
                # Fall back to plain JSON if highlighting fails
                pass
        
        return json_str

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
        """Receives a single message from the specified SQS queue and returns body + receipt handle."""
        print(f"Waiting for message on {queue_url.split('/')[-1]}...")
        messages = self.aws_client.get_sqs_messages(
            queue_url=queue_url,
            max_messages=1,
            visibility_timeout=wait_time_seconds
        )
        if messages:
            message = messages[0]
            # Return both the parsed message body and the receipt handle for deletion
            return message['Body'], message['ReceiptHandle']
        return None

    def _get_capability_request(self) -> dict | None:
        """Polls the fallback capability queue for a message with shorter timeout."""
        # Always use the fallback queue for testing since it receives all capability requests
        # when no specific capability queues are configured
        # Use shorter timeout (10s) for better interactive experience
        result = self._receive_message(self.fallback_capability_queue_url, wait_time_seconds=10)
        if result:
            message_body, receipt_handle = result
            # Store the receipt handle so we can delete the message after processing
            self._current_receipt_handle = receipt_handle
            return message_body
        return None

    def _map_capability_to_node(self, capability_id: str) -> str | None:
        """Maps a capability_id to the corresponding workflow node name."""
        # Simple mapping based on known capability patterns
        capability_to_node_map = {
            "import#create_filingpacks": "Create_Filing_Packs",
            # Add more mappings as needed for other capabilities
        }
        return capability_to_node_map.get(capability_id)
    
    def _send_capability_response(self, original_request: dict, response_file: Path):
        """Loads a response file and sends it back to the orchestrator."""
        with open(response_file, 'r') as f:
            response_payload = json.load(f)
        
        # Create a proper response message structure
        response_message = {
            "workflowInstanceId": original_request.get('workflowInstanceId'),
            "correlationId": original_request.get('correlationId'),
            "workflowDefinitionURI": original_request.get('workflowDefinitionURI'),
            "command": {
                "type": "ASYNC_RESP",
                "id": str(uuid.uuid4()),
                "source": "MockCapabilityService",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "in_reply_to": original_request.get('command', {}).get('id'),
                "status": "SUCCESS" if response_payload.get('status') == 'SUCCESS' else 'ERROR',
                "payload": response_payload.get('data', {})
            }
        }
        
        print(f"Found response file: {response_file.name}")
        print("Response payload:")
        print(self._format_json(response_message, indent=4))
        input("--- Press Enter to send the response back to the orchestrator ---")
        
        self._send_sqs_message(self.command_queue_url, response_message)
        print("Response sent.")
        
        # Delete the processed capability message from the queue
        self._delete_processed_message()

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
        """Verify that the workflow completed successfully by checking DynamoDB state."""
        print(f"Checking workflow state for instance: {correlation_id}")
        
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
                
                # Show key context data that was processed
                if "consignmentImportEnrichedId" in context:
                    print(f"   Enriched Consignment ID: {context['consignmentImportEnrichedId']}")
                if "consignmentImportEnrichedURI" in context:
                    print(f"   Enriched Consignment URI: {context['consignmentImportEnrichedURI']}")
                if "importFilingPacks" in context:
                    filing_packs = context["importFilingPacks"]
                    if isinstance(filing_packs, list):
                        print(f"   Filing Packs Created: {len(filing_packs)} packs")
                    else:
                        print(f"   Filing Packs: {filing_packs}")
                        
                print(f"\n📊 Full Final State:")
                print(f"   Context Keys: {list(context.keys())}")
                print(f"   Data Keys: {list(data.keys())}")
                
            else:
                print("❌ Workflow did not complete within timeout")
                # Get the latest state for debugging
                latest_state = self.workflow_verifier.get_latest_state(correlation_id)
                if latest_state:
                    context = latest_state.get("context", {})
                    current_node = context.get("current_node", "Unknown")
                    print(f"   Last Known Node: {current_node}")
                    print(f"   Available Context Keys: {list(context.keys())}")
                else:
                    print("   No state found in DynamoDB")
                    
        except Exception as e:
            print(f"❌ Error verifying workflow state: {e}")
            print("   This might indicate the workflow failed or is still running")

    def run(self):
        """Executes the interactive test scenario."""
        print("--- Starting Interactive Workflow Scenario ---")
        if not PYGMENTS_AVAILABLE:
            print("💡 Tip: Install 'pygments' for colorized JSON output: pip install pygments")
        correlation_id = f"interactive-test-{uuid.uuid4()}"

        # 1. Upload workflow definition and consignment data
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
                "source": "Interactive-SimplifiedWorkflow",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "consignmentURI": consignment_uri,
                    "_no_cache": True
                }
            }
        }
        
        print("\n--- Sending Start Workflow Command ---")
        print(self._format_json(command_message))
        print("--------------------------------------")
        self._send_sqs_message(self.command_queue_url, command_message)

        # 3. Interactive loop - expect only async_request nodes to send capability messages
        step = 1
        expected_async_steps = 1  # This simplified workflow has only 1 async step: Create_Filing_Packs
        
        while step <= expected_async_steps:
            input(f"\n--- Press Enter to check for capability message (Step {step}) ---")
            capability_message = self._get_capability_request()

            if not capability_message:
                print(f"No capability message received for step {step}.")
                if step == 1:
                    print("This might indicate the workflow hasn't started yet or there's an issue.")
                    break
                else:
                    print("This is expected - no more async capability requests needed.")
                    break

            print("\n--- Received Capability Request ---")
            print(self._format_json(capability_message))
            print("-----------------------------------")

            # Extract capability information to determine response
            capability_id = capability_message.get('command', {}).get('capability_id')
            command_id = capability_message.get('command', {}).get('id')
            
            # Map capability_id to node name (based on workflow definition)
            node_name = self._map_capability_to_node(capability_id)
            
            if not node_name:
                print(f"ERROR: Unknown capability_id '{capability_id}'. Cannot determine response.")
                break
                
            response_file = self.scenario_path / f"{node_name}.json"

            if not response_file.exists():
                print(f"ERROR: No response file found for node '{node_name}'. Cannot continue.")
                print(f"Looked for: {response_file}")
                break
                
            # Load and send the response
            print(f"\n--- Sending Response for {node_name} ---")
            self._send_capability_response(capability_message, response_file)
            step += 1

        # 4. Verify final workflow state
        print("\n--- Verifying Final Workflow State ---")
        self._verify_workflow_completion(correlation_id)
        
        print("\n--- Scenario Finished ---")

if __name__ == "__main__":
    # The scenario is located in the same directory as the script
    current_scenario_path = Path(__file__).parent
    runner = InteractiveTestRunner(current_scenario_path)
    runner.run()
