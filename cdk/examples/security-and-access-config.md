# CCH Flow Orchestrator Security Configuration Guide

This document provides a comprehensive overview of the security and access control features implemented in the CCH Flow Orchestrator CDK stack.

## Command Queue Access Control

The orchestrator's command queue is a central component of the system, and access to it is tightly controlled. There are several ways to grant access to this queue:

### 1. Explicit Authorization via AUTHORIZED_COMMAND_QUEUE_SENDERS

```bash
# External services authorized to send messages to command queue
AUTHORIZED_COMMAND_QUEUE_SENDERS=lambda:function:external-service-1,lambda:function:external-service-2
```

This environment variable accepts a comma-separated list of services that should have access to the command queue. Each service can be specified in several formats:

- **Full ARNs**: `arn:aws:lambda:eu-west-1:123456789012:function:my-function`
- **Lambda shorthand**: `lambda:function:my-function`
- **Role ARNs**: `arn:aws:iam::123456789012:role/service-role`

### 2. Automatic Access for Capability Services

Any service defined in the `CCH_CAPABILITY_` environment variables is automatically granted access to send messages to the command queue. This is inferred from the SQS queue URL:

```bash
CCH_CAPABILITY_REVERSE_IMAGE_SEARCH=https://sqs.eu-west-1.amazonaws.com/123456789012/queue-name
```

The CDK stack extracts the account ID and region from the queue URL and grants Lambda functions in that account permission to send messages to the command queue.

## Capability Queue Configuration

### 1. Standard Capability Queues

Define capability queues with environment variables:

```bash
CCH_CAPABILITY_REVERSE_IMAGE_SEARCH=https://sqs.eu-west-1.amazonaws.com/123456789012/queue-name
CCH_CAPABILITY_IMAGE_METADATA=https://sqs.eu-west-1.amazonaws.com/123456789012/queue-name
```

These variables are:
- Passed directly to the Lambda function's environment
- Used to identify which SQS queue to send requests to for each capability
- Used to automatically grant the associated services access to the command queue

### 2. Auto-Created Mock Queue for Development

In development environments (`env=dev`), a mock capability queue is automatically created:

```
cch-flow-orchestrator-mock-capability-queue-dev-USERNAME
```

This queue is:
- Available to the Lambda function as `CCH_CAPABILITY_MOCK_QUEUE` without configuration
- Used as a fallback when a specific capability queue isn't defined
- Configurable via the `CCH_CAPABILITY_MOCK_QUEUE` environment variable if needed

## Environment-Based Configuration

All of these security features adapt to the deployment environment:

- **Development**: Auto-creates mock queue, simplified setup
- **Production**: Requires explicit configuration of all queues and permissions
- **Custom**: Can mix and match features as needed

## CloudFormation Outputs

To aid in transparency and debugging, the stack creates CloudFormation outputs for:

- Each external service granted access to the command queue
- Each capability service granted access to the command queue
- The URL of the auto-created mock queue (in dev environments)

## Best Practices

1. **Least Privilege**: Only grant access to services that truly need it
2. **Use Environment Variables**: Keep sensitive configuration out of code
3. **Document Access Grants**: Use CloudFormation outputs to track who has access
4. **Dev/Prod Parity**: Keep development and production environments as similar as possible
