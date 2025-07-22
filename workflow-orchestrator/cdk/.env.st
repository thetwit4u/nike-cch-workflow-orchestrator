# Environment variables for CCH Flow Orchestrator CDK deployment
# Copy this file to .env and fill in your values

# AWS Region is set in the CDK stack but can be overridden here
# AWS_REGION=eu-west-1

# External services authorized to send messages to command queue
# Format: comma-separated list of ARNs or function/service names
# AUTHORIZED_COMMAND_QUEUE_SENDERS=lambda:function:external-service-1,lambda:function:external-service-2,123456789012:role/external-service-role
# To specify full ARNs, use:
#AUTHORIZED_COMMAND_QUEUE_SENDERS=arn:aws:lambda:eu-west-1:679172460410:function:external-service-1

# Capability Queue URLs for Workflow Orchestrator
# These are automatically passed to the Lambda function environment
# AWS Lambda functions associated with these queues are automatically granted SendMessage permissions to the command queue
CCH_CAPABILITY_REVERSE_IMAGE_SEARCH=https://sqs.eu-west-1.amazonaws.com/123456789012/cch-capability-ris-dev-david
CCH_CAPABILITY_IMAGE_METADATA=https://sqs.eu-west-1.amazonaws.com/123456789012/cch-capability-im-dev-david

# Add more capability queue URLs as needed
# CCH_CAPABILITY_<CAPABILITY_NAME>=<SQS_QUEUE_URL>
