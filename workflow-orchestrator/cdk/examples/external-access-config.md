# External Service Access to Command Queue

This document explains how to configure external services (like Lambda functions from other projects or AWS accounts) to access the CCH Flow Orchestrator command queue.

## Configuration in .env file

Edit your `.env` file to specify which external services should have access:

```bash
# External services authorized to send messages to command queue
# Format: comma-separated list of ARNs or function/service names
AUTHORIZED_COMMAND_QUEUE_SENDERS=lambda:function:my-external-function,123456789012:role/external-service-role
```

## Supported Formats

You can specify external services in several formats:

1. **Full ARNs**:
   ```
   arn:aws:lambda:eu-west-1:123456789012:function:my-service-function
   arn:aws:iam::123456789012:role/external-service-role
   ```

2. **Shorthand Lambda format**:
   ```
   lambda:function:my-service-function
   ```
   This will be expanded to a full ARN using your account ID and region.

3. **Account and Role format**:
   ```
   123456789012:role/external-service-role
   ```
   This will be converted to an ARN.

## How It Works

When you deploy the CDK stack:

1. The code reads the `AUTHORIZED_COMMAND_QUEUE_SENDERS` environment variable
2. For each service listed, it adds a resource policy to the SQS queue
3. The policy grants `sqs:SendMessage` permission to the specified services
4. For Lambda functions, it uses source ARN conditions to limit access to specific functions

## Example: Granting Access to a Lambda in Another Project

```bash
# In your .env file
AUTHORIZED_COMMAND_QUEUE_SENDERS=arn:aws:lambda:eu-west-1:123456789012:function:external-project-lambda
```

This will allow the specified Lambda function to send messages to your command queue.

## Example: Granting Access to Multiple Services

```bash
# In your .env file
AUTHORIZED_COMMAND_QUEUE_SENDERS=lambda:function:service1,lambda:function:service2,123456789012:role/external-role
```

This will grant access to two Lambda functions in your account and one IAM role in account 123456789012.

## Verifying Access Grants

When the stack is deployed, a CFN output will be created for each granted permission, making it easy to verify which services have been granted access.
