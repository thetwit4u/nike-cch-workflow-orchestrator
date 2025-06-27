import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { aws_sqs as sqs, aws_dynamodb as dynamodb, aws_s3 as s3, aws_lambda as lambda, aws_logs as logs, Duration, Stack, StackProps, Tags, aws_iam as iam } from 'aws-cdk-lib';
import * as python from '@aws-cdk/aws-lambda-python-alpha';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import * as path from 'path';

export class CchWorkflowOrchestratorStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    // Dynamic values from context or environment
    const env = this.node.tryGetContext('env') || process.env.CDK_ENV || 'dev';
    const owner = this.node.tryGetContext('owner') || process.env.CCH_OWNER || 'userid';
    const orgLevel3 = process.env.NIKE_ORG_L3 || 'trade-customs-compliance-hub';

    // Get the list of authorized services for command queue access from environment variable
    const authorizedServicesList = process.env.AUTHORIZED_COMMAND_QUEUE_SENDERS || '';
    
    // Collect all CCH_CAPABILITY_ environment variables for Lambda configuration
    const capabilityEnvVars: { [key: string]: string } = {};
    const capabilityQueueUrls: string[] = [];
    
    Object.keys(process.env).forEach(key => {
      if (key.startsWith('CCH_CAPABILITY_')) {
        const value = process.env[key] || '';
        capabilityEnvVars[key] = value;
        
        // Collect SQS URLs for later granting access to command queue
        if (value && value.includes('sqs.') && value.includes('amazonaws.com')) {
          capabilityQueueUrls.push(value);
        }
      }
    });
    
    // Helper function to extract account ID and queue name from SQS URL
    const extractSqsDetails = (sqsUrl: string): { accountId: string, region: string, queueName: string } | undefined => {
      try {
        // Parse URL like https://sqs.eu-west-1.amazonaws.com/123456789012/queue-name
        const match = sqsUrl.match(/https:\/\/sqs\.([-a-z0-9]+)\.amazonaws\.com\/([0-9]+)\/(.+)/);
        if (match) {
          const details = {
            region: match[1],
            accountId: match[2],
            queueName: match[3]
          };
          console.log(`Successfully parsed SQS URL: ${sqsUrl}`);
          console.log(`  Region: ${details.region}, Account: ${details.accountId}, Queue: ${details.queueName}`);
          return details;
        }
        console.log(`Failed to match SQS URL pattern: ${sqsUrl}`);
      } catch (e) {
        console.log(`Error parsing SQS URL: ${sqsUrl}`, e);
      }
      return undefined;
    };

    Tags.of(this).add('nike-owner', process.env.NIKE_OWNER || 'stijn.liesenborghs@nike.com');
    Tags.of(this).add('nike-distributionlist', process.env.NIKE_DL || 'Lst-gt.scpt.tt.trade.all@Nike.com');
    Tags.of(this).add('nike-environment', env);
    Tags.of(this).add('nike-org-level1', 'scpt');
    Tags.of(this).add('nike-org-level2', 'trade-transportation');
    Tags.of(this).add('nike-org-level3', orgLevel3);
    Tags.of(this).add('nike-owner-id', owner);

    this.templateOptions.description = "Stack for the CCH Workflow Orchestrator PoC";

    const ownerSuffix = owner ? `-${owner}` : '';
    const mainPrefix = 'cch-flow-orchestrator';
    const definitionsBucketPrefix = 'cch-flow-definitions';

    const commandQueueName = `${mainPrefix}-command-queue-${env}${ownerSuffix}`;
    const replyQueueName = `${mainPrefix}-reply-queue-${env}${ownerSuffix}`;
    const importRequestQueueName = `${mainPrefix}-capability-import-request-queue-${env}${ownerSuffix}`;
    const exportRequestQueueName = `${mainPrefix}-capability-export-request-queue-${env}${ownerSuffix}`;
    const mockQueueName = `${mainPrefix}-mock-capability-queue-${env}${ownerSuffix}`;
    const stateTableName = `${mainPrefix}-workflow-state-table-${env}${ownerSuffix}`;
    const eventdataBucketName = `${mainPrefix}-eventdata-bucket-${env}${ownerSuffix}`;
    const definitionsBucketName = `${definitionsBucketPrefix}-${env}${ownerSuffix}`;

    // SQS Queues
    const commandQueue = new sqs.Queue(this, 'OrchestratorCommandQueue', {
      queueName: commandQueueName,
      visibilityTimeout: Duration.seconds(300),
    });

    // Grant access to external services listed in AUTHORIZED_COMMAND_QUEUE_SENDERS
    if (authorizedServicesList) {
      // Split the comma-separated list
      const authorizedServices = authorizedServicesList.split(',').map(s => s.trim()).filter(s => s);
      
      if (authorizedServices.length > 0) {
        console.log(`Granting SendMessage permission to ${authorizedServices.length} external services`);
        
        // Add permissions for each service
        authorizedServices.forEach((service, index) => {
          console.log(`Processing authorization for service: ${service}`);
          
          try {
            // Handle different formats of service identifiers
            if (service.startsWith('arn:')) {
              // Full ARN provided
              if (service.includes(':role/')) {
                // IAM Role ARN
                console.log(`Adding policy for IAM Role: ${service}`);
                
                const roleArn = new iam.ArnPrincipal(service);
                commandQueue.grantSendMessages(roleArn);
                
                // Output the granted permission for logging
                new cdk.CfnOutput(this, `ExternalAccessGrant${index}`, {
                  value: `Granted sqs:SendMessage to IAM Role: ${service}`,
                  description: 'External IAM Role with access to command queue'
                });
              } else if (service.includes(':function:')) {
                // Lambda function ARN
                console.log(`Adding policy for Lambda function: ${service}`);
                
                // Use proper Lambda function invocation permission pattern
                commandQueue.addToResourcePolicy(
                  new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    principals: [new iam.ServicePrincipal('lambda.amazonaws.com')],
                    actions: ['sqs:SendMessage'],
                    resources: [commandQueue.queueArn],
                    conditions: {
                      'ArnEquals': {
                        'aws:SourceArn': service
                      }
                    }
                  })
                );
                
                // Output the granted permission for logging
                new cdk.CfnOutput(this, `ExternalAccessGrant${index}`, {
                  value: `Granted sqs:SendMessage to Lambda: ${service}`,
                  description: 'External Lambda with access to command queue'
                });
              } else {
                // Other ARN type - treat as generic principal
                console.log(`Adding policy for generic ARN: ${service}`);
                
                const arnPrincipal = new iam.ArnPrincipal(service);
                commandQueue.grantSendMessages(arnPrincipal);
                
                // Output the granted permission for logging
                new cdk.CfnOutput(this, `ExternalAccessGrant${index}`, {
                  value: `Granted sqs:SendMessage to ARN: ${service}`,
                  description: 'External service with access to command queue'
                });
              }
            } else if (service.includes(':')) {
              // Shorthand format like 'lambda:function:name' or 'account:role/name'
              const [serviceType, ...rest] = service.split(':');
              
              if (serviceType === 'lambda') {
                // Lambda function shorthand
                const lambdaName = rest.join(':');
                const accountId = process.env.CDK_DEFAULT_ACCOUNT || this.account;
                const region = this.region;
                const lambdaArn = `arn:aws:lambda:${region}:${accountId}:function:${lambdaName}`;
                
                console.log(`Adding policy for Lambda shorthand: ${service} => ${lambdaArn}`);
                
                // Add a condition to only allow the specified Lambda
                commandQueue.addToResourcePolicy(
                  new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    principals: [new iam.ServicePrincipal('lambda.amazonaws.com')],
                    actions: ['sqs:SendMessage'],
                    resources: [commandQueue.queueArn],
                    conditions: {
                      'ArnEquals': {
                        'aws:SourceArn': lambdaArn
                      }
                    }
                  })
                );
                
                // Output the granted permission for logging
                new cdk.CfnOutput(this, `ExternalAccessGrant${index}`, {
                  value: `Granted sqs:SendMessage to Lambda: ${lambdaArn}`,
                  description: 'External Lambda with access to command queue'
                });
              } else {
                // Assume it's an account:role format
                const [accountId, ...roleNameParts] = rest;
                const rolePath = roleNameParts.join(':');
                const roleArn = `arn:aws:iam::${accountId}:${rolePath}`;
                
                console.log(`Adding policy for account:role shorthand: ${service} => ${roleArn}`);
                
                const arnPrincipal = new iam.ArnPrincipal(roleArn);
                commandQueue.grantSendMessages(arnPrincipal);
                
                // Output the granted permission for logging
                new cdk.CfnOutput(this, `ExternalAccessGrant${index}`, {
                  value: `Granted sqs:SendMessage to Role: ${roleArn}`,
                  description: 'External Role with access to command queue'
                });
              }
            } else {
              // Simple name - assume it's a user in the current account
              const userArn = `arn:aws:iam::${this.account}:user/${service}`;
              
              console.log(`Adding policy for user shorthand: ${service} => ${userArn}`);
              
              const userPrincipal = new iam.ArnPrincipal(userArn);
              commandQueue.grantSendMessages(userPrincipal);
              
              // Output the granted permission for logging
              new cdk.CfnOutput(this, `ExternalAccessGrant${index}`, {
                value: `Granted sqs:SendMessage to User: ${userArn}`,
                description: 'External User with access to command queue'
              });
            }
          } catch (error) {
            console.log(`Error processing authorization for service: ${service}`, error);
          }
        });
      }
    }

    // Grant command queue access to all capability services identified from CCH_CAPABILITY_ environment variables
    if (capabilityQueueUrls.length > 0) {
      console.log(`Granting command queue access to ${capabilityQueueUrls.length} capability services`);
      
      capabilityQueueUrls.forEach((queueUrl, index) => {
        try {
          const sqsDetails = extractSqsDetails(queueUrl);
          
          if (sqsDetails) {
            const { accountId, region, queueName } = sqsDetails;
            
            // Instead of using a wildcard, grant permissions to a more specific function pattern
            // Based on the queue name (assuming queue name is related to the function name)
            // Format: arn:aws:lambda:region:account:function:prefix-*
            const queueNameParts = queueName.split('-');
            const capabilityPrefix = queueNameParts[0]; // Usually 'cch'
            const capabilityName = queueNameParts.length > 1 ? queueNameParts[1] : '*'; // 'capability' or specific name
            
            // Create more specific function name pattern based on queue name
            const capabilityServiceArn = `arn:aws:lambda:${region}:${accountId}:function:${capabilityPrefix}-${capabilityName}-*`;
            
            console.log(`Adding policy for capability Lambda pattern: ${capabilityServiceArn}`);
            
            // Instead of using addToResourcePolicy, use the higher-level grantSendMessages method with conditions
            // This ensures proper policy syntax
            commandQueue.addToResourcePolicy(
              new iam.PolicyStatement({
                sid: `AllowCapabilityLambda${index}`, // Adding a unique SID helps avoid policy conflicts
                effect: iam.Effect.ALLOW,
                principals: [new iam.ServicePrincipal('lambda.amazonaws.com')],
                actions: ['sqs:SendMessage'],
                resources: [commandQueue.queueArn],
                conditions: {
                  'ArnLike': {
                    'aws:SourceArn': capabilityServiceArn
                  }
                }
              })
            );
            
            // Output for visibility
            new cdk.CfnOutput(this, `CapabilityAccessGrant${index}`, {
              value: `Granted sqs:SendMessage from Lambda functions matching ${capabilityServiceArn} to command queue`,
              description: 'Capability service with access to command queue'
            });
          } else {
            console.warn(`Skipping invalid SQS URL: ${queueUrl}`);
          }
        } catch (error) {
          console.error(`Error processing capability queue URL: ${queueUrl}`, error);
        }
      });
    }

    const replyQueue = new sqs.Queue(this, 'OrchestratorReplyQueue', {
      queueName: replyQueueName,
      visibilityTimeout: Duration.seconds(300),
    });

    const importRequestQueue = new sqs.Queue(this, 'ImportRequestQueue', {
      queueName: importRequestQueueName,
      visibilityTimeout: Duration.seconds(300),
    });

    const exportRequestQueue = new sqs.Queue(this, 'ExportRequestQueue', {
      queueName: exportRequestQueueName,
      visibilityTimeout: Duration.seconds(300),
    });

    // Create a mock capability queue for development environments
    let mockCapabilityQueue: sqs.Queue | undefined = undefined;
    if (env === 'dev') {
      mockCapabilityQueue = new sqs.Queue(this, 'MockCapabilityQueue', {
        queueName: mockQueueName,
        visibilityTimeout: Duration.seconds(300),
      });
      
      // Log the creation of the mock queue
      console.log(`Creating mock capability queue: ${mockQueueName}`);
      
      // Create a CloudFormation output for the mock queue URL
      new cdk.CfnOutput(this, 'MockCapabilityQueueUrl', {
        value: mockCapabilityQueue.queueUrl,
        description: 'URL of the mock capability queue for development'
      });
    }

    // Role for EventBridge Scheduler to invoke SQS
    const schedulerRole = new iam.Role(this, 'SchedulerExecutionRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
    });

    schedulerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['sqs:SendMessage'],
      resources: [importRequestQueue.queueArn, exportRequestQueue.queueArn],
    }));

    // DynamoDB Table for State Persistence
    const stateTable = new dynamodb.Table(this, 'WorkflowStateTable', {
      tableName: stateTableName,
      partitionKey: {
        name: 'PK',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'SK',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY, // For PoC, destroy on stack deletion
    });

    // S3 Bucket for Workflow Definitions
    const definitionsBucket = new s3.Bucket(this, 'WorkflowDefinitionsBucket', {
      bucketName: definitionsBucketName,
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true, // For PoC, delete objects on stack deletion
    });

    // S3 Bucket for Event Data
    const eventdataBucket = new s3.Bucket(this, 'EventDataBucket', {
      bucketName: eventdataBucketName,
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true, // For PoC, delete objects on stack deletion
    });

    // Workflow Orchestrator Lambda Function
    const orchestratorLambda = new python.PythonFunction(this, 'WorkflowOrchestratorFunction', {
      entry: path.join(__dirname, '../../src'), // path to the python code
      runtime: lambda.Runtime.PYTHON_3_13,
      index: 'app.py', // file with the handler
      handler: 'handler', // function name
      memorySize: 1024,
      environment: {
        STATE_TABLE_NAME: stateTable.tableName,
        DEFINITIONS_BUCKET_NAME: definitionsBucket.bucketName,
        COMMAND_QUEUE_URL: commandQueue.queueUrl,
        COMMAND_QUEUE_ARN: commandQueue.queueArn,
        REPLY_QUEUE_URL: replyQueue.queueUrl,
        IMPORT_QUEUE_URL: importRequestQueue.queueUrl,
        EXPORT_QUEUE_URL: exportRequestQueue.queueUrl,
        VERSION: new Date().toISOString(), // Force code update
        SCHEDULER_ROLE_ARN: schedulerRole.roleArn,
        
        // Set the mock capability queue URL for dev environment, if not already set in env vars
        ...(mockCapabilityQueue && !('CCH_CAPABILITY_MOCK_QUEUE' in capabilityEnvVars) 
          ? { CCH_CAPABILITY_MOCK_QUEUE: mockCapabilityQueue.queueUrl } 
          : {}),
          
        ...capabilityEnvVars, // Spread the capability environment variables (will override the default if explicitly set)
      },
      timeout: Duration.seconds(30),
      bundling: {
        assetExcludes: ['.DS_Store', '.venv', 'venv', 'tests']
      }
    });

    // Add SQS event sources
    orchestratorLambda.addEventSource(new SqsEventSource(commandQueue));
    orchestratorLambda.addEventSource(new SqsEventSource(replyQueue));

    // Grant permissions
    commandQueue.grantConsumeMessages(orchestratorLambda);
    replyQueue.grantConsumeMessages(orchestratorLambda);
    importRequestQueue.grantSendMessages(orchestratorLambda);
    exportRequestQueue.grantSendMessages(orchestratorLambda);
    
    // Grant permissions for mock capability queue if created
    if (mockCapabilityQueue) {
      mockCapabilityQueue.grantSendMessages(orchestratorLambda);
      mockCapabilityQueue.grantConsumeMessages(orchestratorLambda);
    }

    // Grant EventBridge Scheduler permissions
    orchestratorLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['iam:PassRole'],
      resources: [schedulerRole.roleArn],
    }));
    orchestratorLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['scheduler:CreateSchedule', 'scheduler:DeleteSchedule', 'scheduler:GetSchedule'],
      resources: [`arn:aws:scheduler:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:schedule/cch-workflow-schedules/*`],
    }));

    new logs.LogRetention(this, 'OrchestratorLogRetention', {
      logGroupName: orchestratorLambda.logGroup.logGroupName,
      retention: logs.RetentionDays.ONE_DAY,
    });

    // --- Capability Mock Service ---

    // Mock Service Lambda Function
    const mockServiceLambda = new python.PythonFunction(this, 'CapabilityMockServiceFunction', {
      entry: path.join(__dirname, '../../capability-mock-service'),
      runtime: lambda.Runtime.PYTHON_3_13,
      index: 'app.py',
      handler: 'handler',
      environment: {
        REPLY_QUEUE_URL: replyQueue.queueUrl,
        VERSION: new Date().toISOString(), // Force code update
      },
      timeout: Duration.seconds(30),
    });

    // Add SQS event sources for mock service
    mockServiceLambda.addEventSource(new SqsEventSource(importRequestQueue));
    mockServiceLambda.addEventSource(new SqsEventSource(exportRequestQueue));

    // Grant permissions for mock service
    replyQueue.grantSendMessages(mockServiceLambda);
    eventdataBucket.grantRead(mockServiceLambda);

    new logs.LogRetention(this, 'MockServiceLogRetention', {
      logGroupName: mockServiceLambda.logGroup.logGroupName,
      retention: logs.RetentionDays.ONE_DAY,
    });

    // The permissions for authorized external services are already handled above
    // No need for additional grants here
  }
}
