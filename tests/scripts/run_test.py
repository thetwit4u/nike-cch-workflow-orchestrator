import boto3
import json
import uuid
import argparse
import os
import time
import sys
import re
try:
    from colorama import init, Fore, Style
    init(autoreset=True)
except ImportError:
    class Dummy:
        RESET = CYAN = RED = ''
    Fore = Style = Dummy()
try:
    from pygments import highlight
    from pygments.lexers import JsonLexer
    from pygments.formatters import TerminalFormatter
except ImportError:
    highlight = None

# --- Configuration ---
# Require CCH_ENV and CCH_OWNER to be set, or use defaults matching cdk.json
CCH_ENV = os.environ.get("CCH_ENV", "dev")
CCH_OWNER = os.environ.get("CCH_OWNER", "defaultowner")
if os.environ.get("CCH_ENV") is None or os.environ.get("CCH_OWNER") is None:
    print("Warning: CCH_ENV or CCH_OWNER not set. Using defaults: CCH_ENV=dev, CCH_OWNER=defaultowner (matches cdk.json).")

BUCKET_NAME = f"cch-{CCH_ENV}-{CCH_OWNER}-workflow-definitions-bucket"
COMMAND_QUEUE_NAME = f"cch-{CCH_ENV}-{CCH_OWNER}-orchestrator-command-queue"
# The root path of the workflow definitions directory
DEFINITIONS_ROOT = "workflow-definitions"
# A unique part of the Lambda function's name to search for
LAMBDA_FUNCTION_NAME_SUBSTRING = "WorkflowOrchestratorFunc"
# How long to monitor logs for, in seconds
LOG_MONITOR_TIMEOUT = 120
# How long to wait between log polls, in seconds
LOG_POLL_INTERVAL = 5

def format_log_line(ts, component, message, is_error=False):
    ts_str = ts.strftime('%H:%M:%S,%f')[:-3]  # HH:MM:SS,mmm
    comp_str = f"[{component}]" if component else ""
    if is_error:
        color = Fore.RED
    else:
        # Use custom orange (#fd631d) for component name
        ORANGE = '\033[38;2;253;99;29m'
        color = ORANGE if component else ''
    return f"{Fore.WHITE}{ts_str} {color}{comp_str}{Style.RESET_ALL} {message}"

def print_log_event(event):
    msg = event['message'].strip()
    ts = event['timestamp'] / 1000
    import datetime
    dt = datetime.datetime.fromtimestamp(ts)
    # Remove leading timestamp and log level tags (e.g., '2025-06-13 21:14:14,283 [INFO]')
    msg = re.sub(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}\s*', '', msg)
    msg = re.sub(r'^\[(INFO|ERROR|WARN|DEBUG|TRACE)\]\s*', '', msg)
    # Remove [WorkflowID: ...] prefix if present (at start or after component)
    msg = re.sub(r'^\[WorkflowID: [^\]]+\]\s*', '', msg)
    msg = re.sub(r'\[WorkflowID: [^\]]+\]\s*', '', msg)
    # Robust: search for any JSON object in the message
    json_match = re.search(r'(\{.*\})', msg)
    if json_match:
        try:
            import json
            json_obj = json.loads(json_match.group(1))
            # SQS event body extraction
            if (
                isinstance(json_obj, dict)
                and 'Records' in json_obj
                and isinstance(json_obj['Records'], list)
                and json_obj['Records']
                and json_obj['Records'][0].get('eventSource') == 'aws:sqs'
            ):
                body_str = json_obj['Records'][0].get('body', '{}')
                try:
                    body_json = json.loads(body_str)
                    pretty_body = json.dumps(body_json, indent=2)
                    if highlight:
                        pretty_body = highlight(pretty_body, JsonLexer(), TerminalFormatter())
                except Exception:
                    pretty_body = body_str
                print(format_log_line(dt, 'SQS_BODY', pretty_body, False))
                return
            # Mermaid diagram extraction
            if isinstance(json_obj, dict) and 'diagram' in json_obj:
                MERMAID_COLOR = '\033[38;2;207;255;0m'  # #cfff00
                RESET = '\033[0m'
                print("\n--- Mermaid Diagram (copy below into a Mermaid viewer) ---\n")
                print(f"{MERMAID_COLOR}{json_obj['diagram']}{RESET}")
                print("\n--- End Mermaid Diagram ---\n")
                return
        except Exception:
            pass
    # Regex for component: [component] or [component.sub]
    m = re.match(r'\[([^\]]+)\]\s*(.*)', msg)
    component = m.group(1) if m else ''
    rest = m.group(2) if m else msg
    # Remove [WorkflowID: ...] prefix if present after component
    rest = re.sub(r'^\[WorkflowID: [^\]]+\]\s*', '', rest)
    rest = re.sub(r'\[WorkflowID: [^\]]+\]\s*', '', rest)
    is_error = '[ERROR]' in msg or '[Error]' in msg
    # Try to pretty-print JSON if present
    json_match = re.search(r'({.*})', rest)
    if json_match and highlight:
        try:
            import json
            json_obj = json.loads(json_match.group(1))
            pretty_json = json.dumps(json_obj, indent=2)
            rest = rest.replace(json_match.group(1),
                highlight(pretty_json, JsonLexer(), TerminalFormatter()))
        except Exception:
            pass
    # Test colorama output
    if not hasattr(Fore, 'CYAN') or not hasattr(Style, 'RESET_ALL'):
        print('[WARN] colorama is not working, output will not be colored.')
    print(format_log_line(dt, component, rest, is_error))

def get_log_group_name() -> str | None:
    """
    Finds the CloudWatch log group name for our orchestrator Lambda.
    """
    try:
        print(f"Searching for Lambda function containing '{LAMBDA_FUNCTION_NAME_SUBSTRING}'...")
        lambda_client = boto3.client("lambda", region_name="eu-west-1")
        paginator = lambda_client.get_paginator('list_functions')
        for page in paginator.paginate():
            for function in page['Functions']:
                if LAMBDA_FUNCTION_NAME_SUBSTRING in function['FunctionName']:
                    log_group_name = f"/aws/lambda/{function['FunctionName']}"
                    print(f"Found log group: {log_group_name}")
                    return log_group_name
        print("Error: Could not find orchestrator Lambda function.")
        return None
    except Exception as e:
        print(f"Error finding Lambda function: {e}")
        return None

def monitor_workflow_logs(log_group_name: str, workflow_id: str):
    """
    Polls and prints logs for a specific workflow instance in near real-time.
    """
    print(f"\n--- Monitoring logs for Workflow ID: {workflow_id} ---")
    print(f"(Will monitor for up to {LOG_MONITOR_TIMEOUT} seconds. Press Ctrl+C to exit early)")
    logs_client = boto3.client("logs", region_name="eu-west-1")
    start_time = int(time.time() * 1000) - 10000 # Start 10s in the past
    end_time = start_time + (LOG_MONITOR_TIMEOUT * 1000)
    next_token = None
    end_log_pattern = re.compile(rf"\[WorkflowID: {re.escape(workflow_id)}\] Workflow has ended\.")
    try:
        while time.time() * 1000 < end_time:
            kwargs = {
                'logGroupName': log_group_name,
                'filterPattern': f'"{workflow_id}"',
                'startTime': start_time,
            }
            if next_token:
                kwargs['nextToken'] = next_token
            response = logs_client.filter_log_events(**kwargs)
            events = sorted(response['events'], key=lambda e: e['timestamp'])
            for event in events:
                msg = event['message'].strip()
                # Detect new single-line Mermaid diagram log (print immediately in color)
                mermaid_match = re.search(r'\[MERMAID_DIAGRAM\] (\{.*\})', msg)
                if mermaid_match:
                    try:
                        import json
                        MERMAID_COLOR = '\033[38;2;207;255;0m'  # #cfff00
                        RESET = '\033[0m'
                        diagram_json = json.loads(mermaid_match.group(1))
                        print("\n--- Mermaid Diagram (copy below into a Mermaid viewer) ---\n")
                        print(f"{MERMAID_COLOR}{diagram_json['diagram']}{RESET}")
                        print("\n--- End Mermaid Diagram ---\n")
                    except Exception as e:
                        print(f"[WARN] Failed to parse Mermaid diagram JSON: {e}")
                    continue
                print_log_event(event)
                start_time = event['timestamp'] + 1
                # Exit if workflow has ended for this instance
                if end_log_pattern.search(msg):
                    print(f"Workflow has ended for instance {workflow_id}. Exiting log monitoring.")
                    return
            next_token = response.get('nextToken')
            time.sleep(1)  # 1 second for near real-time
    except KeyboardInterrupt:
        print("\n--- Stopped monitoring logs ---")
    except Exception as e:
        print(f"\n--- Error monitoring logs: {e} ---")
    print("--- Finished monitoring logs ---")

def get_queue_url(queue_name: str) -> str:
    """
    Dynamically fetches the URL for a given SQS queue name.
    
    :param queue_name: The name of the SQS queue.
    :return: The URL of the queue.
    """
    sqs = boto3.client("sqs", region_name="eu-west-1")
    try:
        print(f"Fetching URL for queue '{queue_name}'...")
        response = sqs.get_queue_url(QueueName=queue_name)
        url = response['QueueUrl']
        print(f"Found queue URL: {url}")
        return url
    except Exception as e:
        print(f"Error fetching queue URL for '{queue_name}': {e}")
        raise

def upload_definition(file_path: str):
    """
    Uploads a workflow definition file to the S3 bucket.

    :param file_path: The local path to the workflow definition YAML file.
    """
    s3 = boto3.client("s3")
    s3_key = os.path.basename(file_path)
    try:
        print(f"Uploading '{file_path}' to S3 bucket '{BUCKET_NAME}' with key '{s3_key}'...")
        s3.upload_file(file_path, BUCKET_NAME, s3_key)
        print("Upload successful.")
        return s3_key
    except Exception as e:
        print(f"Error uploading to S3: {e}")
        raise

def upload_sampledata(file_path: str):
    """
    Uploads a sample data file (e.g., ASN event or delivery sample) to the S3 eventdata bucket.
    :param file_path: The local path to the sample data file.
    :return: The S3 key of the uploaded file.
    """
    s3 = boto3.client("s3")
    sample_data_bucket = f"cch-{CCH_ENV}-{CCH_OWNER}-eventdata-bucket"
    s3_key = os.path.basename(file_path)
    try:
        print(f"Uploading sample data '{file_path}' to S3 bucket '{sample_data_bucket}' with key '{s3_key}'...")
        s3.upload_file(file_path, sample_data_bucket, s3_key)
        print("Sample data upload successful.")
        return s3_key
    except Exception as e:
        print(f"Error uploading sample data to S3: {e}")
        raise

def start_workflow(s3_key: str, queue_url: str, instance_id: str, sample_data_s3_key: str, delivery_data_s3_key: str) -> str:
    """
    Sends a 'startWorkflow' command to the SQS queue.
    :param s3_key: The S3 key of the workflow definition to start.
    :param queue_url: The full URL of the target SQS queue.
    :param instance_id: The predictable ID for this workflow instance.
    :param sample_data_s3_key: The S3 key of the uploaded sample ASN data file.
    :param delivery_data_s3_key: The S3 key of the uploaded delivery sample data file.
    :return: The workflow instance ID used.
    """
    sqs = boto3.client("sqs", region_name="eu-west-1")

    # Use values from sample-data/asn-inbound-sample.json
    event_id = "A1B2C3D4-E5F6-7890-1234-567890ABCDEF"
    event_timestamp = "2025-06-11T10:00:00.000Z"
    shipment_id = "5000164201-MEM"
    correlation_id = "8bf7f5c4-90f0-46b5-a1d5-5125367ee3a7"
    sample_data_bucket = f"cch-{CCH_ENV}-{CCH_OWNER}-eventdata-bucket"
    event_data_uri = f"s3://{sample_data_bucket}/{sample_data_s3_key}"
    # delivery_data_uri = f"s3://{sample_data_bucket}/{delivery_data_s3_key}"  # No longer needed

    command_message = {
        "workflowInstanceId": instance_id,
        "correlationId": correlation_id,
        "workflowDefinitionURI": f"s3://{BUCKET_NAME}/{s3_key}",
        "sourceEvent": {
            "eventType": "asn-inbound",
            "eventId": event_id,
            "timestamp": event_timestamp,
            "eventDataURI": event_data_uri
        },
        "command": {
            "type": "EVENT",
            "id": "cmd-a1b2-c3d4-e5f6",
            "source": "TradeFlowController",
            "timestamp": event_timestamp,
            "payload": {
                "shipmentId": shipment_id,
                "originCountry": "VN",
                "destinationCountry": "US",
                "asnUri": event_data_uri
            }
        }
        # deliverySampleURI is no longer included
    }

    try:
        print(f"Sending 'EVENT' command for instance '{instance_id}'...")
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(command_message)
        )
        print("Command sent successfully.")
        print(f"Workflow Instance ID: {instance_id}")
        return instance_id
    except Exception as e:
        print(f"Error sending message to SQS: {e}")
        raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a workflow orchestrator test.")
    parser.add_argument(
        "definition_file", 
        nargs="?",
        default="import_vn_memphis_v1.yaml",
        help="The name of the workflow definition file (default: 'import_vn_memphis_v1.yaml')"
    )
    parser.add_argument(
        "--instance-id",
        default="test-instance-123",
        help="A predictable instance ID for the workflow. Defaults to 'test-instance-123'."
    )
    args = parser.parse_args()

    # Construct the full path to the definition file
    local_file_path = os.path.join(DEFINITIONS_ROOT, args.definition_file)

    if not os.path.exists(local_file_path):
        print(f"Error: Definition file not found at '{local_file_path}'")
        exit(1)

    # 0. Get the queue URL dynamically
    command_queue_url = get_queue_url(COMMAND_QUEUE_NAME)

    # 1. Upload the definition
    uploaded_s3_key = upload_definition(local_file_path)
    # 1b. Upload the sample ASN data
    sample_data_path = os.path.join("sample-data", "asn-inbound-sample.json")
    sample_data_s3_key = upload_sampledata(sample_data_path)
    # 1c. Upload the delivery sample data
    delivery_data_path = os.path.join("sample-data", "delivery-sample.json")
    delivery_data_s3_key = upload_sampledata(delivery_data_path)
    # 2. Start the workflow
    instance_id = start_workflow(uploaded_s3_key, command_queue_url, args.instance_id, sample_data_s3_key, delivery_data_s3_key)

    # NOTE: When mocking the async response for 'import#enrichment',
    # be sure to include 'import_enrichment_uri' in the payload, e.g.:
    # {
    #   "import_enrichment_uri": "mock-uri-value",
    #   ... (other response data) ...
    # }

    # Retry loop: wait up to 30 seconds for log events containing the instance-id
    print("Waiting for log events containing the instance-id to appear (max 30 seconds)...")
    logs_client = boto3.client("logs", region_name="eu-west-1")
    log_group = get_log_group_name()
    if not log_group:
        print("[ERROR] Could not find the orchestrator Lambda log group. Please check your deployment and naming.")
        exit(1)
    found_log_event = False
    for i in range(30):
        response = logs_client.filter_log_events(
            logGroupName=log_group,
            filterPattern=f'"{instance_id}"',
            startTime=int(time.time() * 1000) - 300000  # look back 5 minutes
        )
        events = response.get('events', [])
        if events:
            print(f"Log events found. Proceeding to log monitoring.")
            found_log_event = True
            break
        else:
            time.sleep(1)
    if not found_log_event:
        print("[WARN] No log events containing the instance-id were found after 30 seconds. Proceeding to log monitoring anyway.")

    # 3. Monitor the logs
    if log_group and instance_id:
        monitor_workflow_logs(log_group, instance_id)

    print("\nTest execution triggered.") 