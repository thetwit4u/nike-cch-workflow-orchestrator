# Capability Queue Configuration Guide

This document explains how to configure the capability queue URLs for the CCH Workflow Orchestrator.

## What are Capability Queues?

The Workflow Orchestrator communicates with external "capability" services through SQS queues. Each capability service has its own SQS queue that the orchestrator uses to send requests.

## Configuration

Capability queue URLs are passed to the Lambda function as environment variables with the prefix `CCH_CAPABILITY_`. The naming convention follows the pattern:

```
CCH_CAPABILITY_<CAPABILITY_NAME>=<SQS_QUEUE_URL>
```

Where:
- `<CAPABILITY_NAME>` is the identifier used in workflow definitions to reference the capability
- `<SQS_QUEUE_URL>` is the full URL of the SQS queue for that capability service

### Example

If your workflow definition has a node that calls a capability called "reverse_image_search":

```yaml
nodes:
  image_search:
    type: async_request
    capability: reverse_image_search
    # ...other properties...
```

Then you need to define an environment variable:

```
CCH_CAPABILITY_REVERSE_IMAGE_SEARCH=https://sqs.eu-west-1.amazonaws.com/123456789012/capability-ris-queue
```

## Mock Queue

For development environments (`dev`), a mock capability queue is automatically created by the CDK stack. This queue is available to the Lambda function as `CCH_CAPABILITY_MOCK_QUEUE` without any additional configuration.

If you want to override this auto-created queue, you can set the environment variable explicitly:

```
CCH_CAPABILITY_MOCK_QUEUE=https://sqs.eu-west-1.amazonaws.com/123456789012/my-custom-mock-queue
```

This mock queue is useful for development and testing, as it allows you to deploy the orchestrator even if not all capability services are available.

## Setup with .env File

You can configure the capability queues in your `.env` file:

1. Copy `.env.example` to `.env`
2. Add or modify the `CCH_CAPABILITY_` variables as needed
3. Run the CDK deployment using `./cdk-with-env.sh deploy`

Example `.env` file:
```bash
# ... other environment variables ...

# Capability Queue URLs
CCH_CAPABILITY_REVERSE_IMAGE_SEARCH=https://sqs.eu-west-1.amazonaws.com/123456789012/cch-capability-ris-dev-david
CCH_CAPABILITY_IMAGE_METADATA=https://sqs.eu-west-1.amazonaws.com/123456789012/cch-capability-im-dev-david
CCH_CAPABILITY_MOCK_QUEUE=https://sqs.eu-west-1.amazonaws.com/123456789012/cch-capability-mock-queue-dev-david
```

## How It Works

1. The CDK stack collects all environment variables starting with `CCH_CAPABILITY_`
2. These variables are passed directly to the Lambda function's environment
3. The Lambda function uses these variables to determine which queue to send requests to for each capability

## Automatic Command Queue Access

When you define a `CCH_CAPABILITY_` environment variable with an SQS queue URL, the CDK stack automatically:

1. Extracts the AWS account ID and region from the queue URL
2. Grants Lambda functions in that account permission to send messages to the command queue
3. Creates a CloudFormation output showing the permission grant

This means that the Lambda functions processing messages from the capability queues can automatically send messages back to the orchestrator's command queue without any additional configuration.
