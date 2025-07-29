# Environment variables for CCH Flow Orchestrator CDK deployment
# Copy this file to .env and fill in your values

# AWS Region is set in the CDK stack but can be overridden here
# AWS_REGION=eu-west-1

# External services authorized to send messages to command queue
# Format: comma-separated list of ARNs or function/service names
# AUTHORIZED_COMMAND_QUEUE_SENDERS=lambda:function:external-service-1,lambda:function:external-service-2,123456789012:role/external-service-role
# To specify full ARNs, use:
AUTHORIZED_COMMAND_QUEUE_SENDERS=arn:aws:iam::679172460410:role/FlowControllerInitiatorLa-LambdaExecutionRoleD5C260-G4bJLS8v0Xok

# Capability Queue URLs for Workflow Orchestrator
# These are automatically passed to the Lambda function environment
# AWS Lambda functions associated with these queues are automatically granted SendMessage permissions to the command queue
CCH_CAPABILITY_IMPORT=https://sqs.us-east-1.amazonaws.com/679172460410/cch-enrichment


# If not set, the fallback sns topic with SQS queue is created automatically.
SNS_TOPIC_CCH_EVENTS_ARN=""

# Add more capability queue URLs as needed
# CCH_CAPABILITY_<CAPABILITY_NAME>=<SQS_QUEUE_URL>
