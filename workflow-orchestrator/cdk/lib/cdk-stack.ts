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
          return {
            region: match[1],
            accountId: match[2],
            queueName: match[3]
          };
        }
      } catch (e) {
        console.log(`Failed to parse SQS URL: ${sqsUrl}`);
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
      const authorizedServices = authorizedServicesList.split(',').map(s => s.trim());
      
      if (authorizedServices.length > 0) {
        console.log(`Granting SendMessage permission to ${authorizedServices.length} external services`);
        
        // Add permissions for each service
        authorizedServices.forEach((service, index) => {
          let principal: iam.IPrincipal;
          
          // Handle different formats of service identifiers
          if (service.startsWith('arn:')) {
            // Full ARN provided
            if (service.includes(':role/')) {
              // IAM Role ARN
              principal = new iam.ArnPrincipal(service);
            } else if (service.includes(':function:')) {
              // Lambda function ARN
              principal = new iam.ServicePrincipal('lambda.amazonaws.com');
              // Also grant to the Lambda's execution role
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
              return; // Skip the common policy statement at the end
            } else {
              // Other ARN type - treat as generic principal
              principal = new iam.ArnPrincipal(service);
            }
          } else if (service.includes(':')) {
            // Shorthand format like 'lambda:function:name' or 'account:role/name'
            const [serviceType, ...rest] = service.split(':');
            if (serviceType === 'lambda') {
              principal = new iam.ServicePrincipal('lambda.amazonaws.com');
              // Will need to determine the actual Lambda ARN
              const lambdaName = rest.join(':');
              const accountId = process.env.CDK_DEFAULT_ACCOUNT || this.account;
              const region = this.region;
              
              // Add a condition to only allow the specified Lambda
              commandQueue.addToResourcePolicy(
                new iam.PolicyStatement({
                  effect: iam.Effect.ALLOW,
                  principals: [principal],
                  actions: ['sqs:SendMessage'],
                  resources: [commandQueue.queueArn],
                  conditions: {
                    'ArnEquals': {
                      'aws:SourceArn': `arn:aws:lambda:${region}:${accountId}:${lambdaName}`
                    }
                  }
                })
              );
              return; // Skip the common policy statement at the end
            } else {
              // Assume it's an account:role format
              const [accountId, ...roleNameParts] = rest;
              const roleName = roleNameParts.join(':');
              principal = new iam.ArnPrincipal(`arn:aws:iam::${accountId}:${roleName}`);
            }
          } else {
            // Simple name - assume it's a user
            principal = new iam.ArnPrincipal(`arn:aws:iam::${this.account}:user/${service}`);
          }
          
          // Add the policy statement to the queue
          commandQueue.addToResourcePolicy(
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              principals: [principal],
              actions: ['sqs:SendMessage'],
              resources: [commandQueue.queueArn]
            })
          );
          
          // Output the granted permission for logging
          new cdk.CfnOutput(this, `ExternalAccessGrant${index}`, {
            value: `Granted sqs:SendMessage to ${service}`,
            description: 'External service with access to command queue'
          });
        });
      }
    }

    // Grant command queue access to all capability services identified from CCH_CAPABILITY_ environment variables
    if (capabilityQueueUrls.length > 0) {
      console.log(`Granting command queue access to ${capabilityQueueUrls.length} capability services`);
      
      capabilityQueueUrls.forEach((queueUrl, index) => {
        const sqsDetails = extractSqsDetails(queueUrl);
        
        if (sqsDetails) {
          const { accountId, region, queueName } = sqsDetails;
          
          // Create an ARN for the queue's execution role
          // SQS queues are typically accessed by Lambda functions, so we grant access to the Lambda service
          const capabilityServiceArn = `arn:aws:lambda:${region}:${accountId}:function:*`;
          
          // Add resource policy to allow the capability service to send messages to the command queue
          commandQueue.addToResourcePolicy(
            new iam.PolicyStatement({
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
            value: `Granted sqs:SendMessage from account ${accountId} Lambda functions to command queue`,
            description: 'Capability service with access to command queue'
          });
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
        assetExcludes: ['.DS_Store', '.venv', 'tests']
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

    // Grant send message access to authorized external services
    if (authorizedServicesList) {
      const authorizedServices = authorizedServicesList.split(',');
      for (const serviceArn of authorizedServices) {
        commandQueue.grantSendMessages(new iam.ServicePrincipal(serviceArn));
      }
    }
  }
}
