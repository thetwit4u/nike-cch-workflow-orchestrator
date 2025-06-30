import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { aws_sqs as sqs, aws_dynamodb as dynamodb, aws_s3 as s3, aws_lambda as lambda, aws_logs as logs, Duration, Stack, StackProps, Tags, aws_iam as iam, aws_apigateway as apigateway, aws_scheduler as scheduler } from 'aws-cdk-lib';
import * as python from '@aws-cdk/aws-lambda-python-alpha';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import * as path from 'path';

interface CchWorkflowOrchestratorStackProps extends StackProps {
  isTest?: boolean;
}

export class CchWorkflowOrchestratorStack extends Stack {
  constructor(scope: Construct, id: string, props?: CchWorkflowOrchestratorStackProps) {
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
    
    const mockQueueName = `${mainPrefix}-mock-capability-queue-${env}${ownerSuffix}`;
    const stateTableName = `${mainPrefix}-workflow-state-table-${env}${ownerSuffix}`;
    const eventdataBucketName = `${mainPrefix}-eventdata-bucket-${env}${ownerSuffix}`;
    const definitionsBucketName = `${definitionsBucketPrefix}-${env}${ownerSuffix}`;
    const schedulerGroupName = `${mainPrefix}-schedules-${env}${ownerSuffix}`;

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
    const schedulerRole = new iam.Role(this, 'SchedulerRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
    });

    // Grant the scheduler role permission to send messages to the command queue.
    // This high-level grant method automatically handles the necessary SQS and KMS permissions.
    commandQueue.grantSendMessages(schedulerRole);

    // Create the schedule group that the client expects
    const schedulerGroup = new scheduler.CfnScheduleGroup(this, 'SchedulerGroup', {
      name: schedulerGroupName,
    });

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

    // --- IAM Role for Test Execution ---
    // This role is assumed by the BDD test runner to get necessary permissions.
    const testExecutorArn = process.env.TEST_EXECUTOR_ARN;
    if (testExecutorArn) {
      const testExecutorRole = new iam.Role(this, 'TestExecutorRole', {
        roleName: `${mainPrefix}-test-executor-role-${env}${ownerSuffix}`,
        assumedBy: new iam.ArnPrincipal(testExecutorArn),
        description: 'Role for BDD test execution, providing access to necessary AWS resources.',
      });

      // The default trust policy only allows sts:AssumeRole.
      // We must explicitly add sts:TagSession for roles assumed via AWS SSO.
      const defaultPolicy = testExecutorRole.assumeRolePolicy;
      if (defaultPolicy) {
        defaultPolicy.addStatements(
          new iam.PolicyStatement({
            actions: ['sts:TagSession'],
            principals: [new iam.ArnPrincipal(testExecutorArn)],
            effect: iam.Effect.ALLOW,
          })
        );
      }

      // Grant the test executor role write access to the S3 ingest bucket
      eventdataBucket.grantWrite(testExecutorRole);

      // Grant the test executor role read access to the state table for verification
      testExecutorRole.addToPolicy(new iam.PolicyStatement({
        sid: 'AllowTestRunnerToReadStateTable', // A unique ID to force a change
        effect: iam.Effect.ALLOW,
        actions: [
          'dynamodb:GetItem',
          'dynamodb:Query',
          'dynamodb:DescribeTable'
        ],
        resources: [stateTable.tableArn],
      }));

      // Grant the test executor role send access to the command queue
      commandQueue.grantSendMessages(testExecutorRole);

      // Grant the test executor role permissions to write to the S3 buckets
      eventdataBucket.grantReadWrite(testExecutorRole);
      definitionsBucket.grantReadWrite(testExecutorRole);
      
      // Grant the test executor role permissions to send messages to the mock capability queue
      if (mockCapabilityQueue) {
        mockCapabilityQueue.grantSendMessages(testExecutorRole);
      }

      new cdk.CfnOutput(this, 'TestExecutorRoleArn', {
        value: testExecutorRole.roleArn,
        description: 'ARN of the IAM role for the BDD test executor'
      });
    }

    // Workflow Orchestrator Lambda Function
    const orchestratorLambda = new python.PythonFunction(this, 'OrchestratorLambda', {
      functionName: `${mainPrefix}-lambda-${env}${ownerSuffix}`,
      entry: path.join(__dirname, '../../src'), // Point to the entire src directory
      runtime: lambda.Runtime.PYTHON_3_13,
      index: 'app.py',
      handler: 'handler',
      memorySize: 1024,
      environment: {
        STATE_TABLE_NAME: stateTable.tableName,
        DEFINITIONS_BUCKET_NAME: definitionsBucket.bucketName,
        COMMAND_QUEUE_URL: commandQueue.queueUrl,
        COMMAND_QUEUE_ARN: commandQueue.queueArn,
        VERSION: new Date().toISOString(), // Force code update
        SCHEDULER_ROLE_ARN: schedulerRole.roleArn,
        SCHEDULER_GROUP_NAME: schedulerGroupName,
        LOG_LEVEL: 'INFO',
        ...capabilityEnvVars
      },
      timeout: Duration.seconds(300),
      bundling: {
        assetExcludes: ['.DS_Store', '.venv', 'tests']
      }
    });

    // Add SQS event source to trigger the Lambda
    orchestratorLambda.addEventSource(new SqsEventSource(commandQueue, {
      batchSize: 1
    }));
    
    // Grant the Lambda function permissions to consume messages from the queue
    commandQueue.grantConsumeMessages(orchestratorLambda);

    // Grant the Lambda function permissions to read from the S3 bucket
    definitionsBucket.grantRead(orchestratorLambda);
    eventdataBucket.grantRead(orchestratorLambda);
    
    stateTable.grantReadWriteData(orchestratorLambda);
    
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
      actions: ['scheduler:CreateSchedule', 'scheduler:UpdateSchedule', 'scheduler:DeleteSchedule', 'scheduler:GetSchedule'],
      resources: [`arn:aws:scheduler:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:schedule/${schedulerGroupName}/*`],
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
        VERSION: new Date().toISOString(), // Force code update
      },
      timeout: Duration.seconds(30),
    });

    // Add SQS event sources for mock service
    if (mockCapabilityQueue) {
      mockServiceLambda.addEventSource(new SqsEventSource(mockCapabilityQueue));
    }

    // Grant permissions for mock service
    
    eventdataBucket.grantRead(mockServiceLambda);

    new logs.LogRetention(this, 'MockServiceLogRetention', {
      logGroupName: mockServiceLambda.logGroup.logGroupName,
      retention: logs.RetentionDays.ONE_DAY,
    });

    // The permissions for authorized external services are already handled above
    // No need for additional grants here

    // --- STACK OUTPUTS ---
    // These are useful for connecting other services or for test automation

    new cdk.CfnOutput(this, 'CheckpointTableName', {
      value: stateTable.tableName,
      description: 'Name of the DynamoDB table for workflow state',
    });

    new cdk.CfnOutput(this, 'IngestBucketName', {
      value: eventdataBucket.bucketName,
      description: 'Name of the S3 bucket for event data (ingest)'
    });

    new cdk.CfnOutput(this, 'DefinitionsBucketName', {
      value: definitionsBucket.bucketName,
      description: 'Name of the S3 bucket for workflow definitions'
    });

    new cdk.CfnOutput(this, 'OrchestratorCommandQueueUrl', {
      value: commandQueue.queueUrl,
      description: 'URL of the orchestrator command SQS queue'
    });

    // --- TEST-ONLY RESOURCES ---
    // The following resources are only created when deploying for testing.
    // For BDD tests to work, the stack must be deployed with: cdk deploy -c test=true
    if (props?.isTest) {
      
      // 1. Create a DynamoDB table to store mock configurations for tests.
      const mockConfigTable = new dynamodb.Table(this, 'MockConfigTable', {
        tableName: `${mainPrefix}-mock-config-table-${env}${ownerSuffix}`,
        partitionKey: { name: 'capability_id', type: dynamodb.AttributeType.STRING },
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        removalPolicy: cdk.RemovalPolicy.DESTROY, // Automatically delete table on stack destroy
      });

      // 2. Create a single SQS queue to receive all capability calls during tests.
      const mockCapabilityQueue = new sqs.Queue(this, 'MockCapabilityTestQueue', {
        queueName: `${mainPrefix}-mock-capability-test-queue-${env}${ownerSuffix}`,
        visibilityTimeout: Duration.seconds(300),
      });

      // 3. Create the Mock Service Lambda.
      const mockServiceLambda = new python.PythonFunction(this, 'MockCapabilityServiceLambda', {
        functionName: `${mainPrefix}-mock-service-${env}${ownerSuffix}`,
        entry: path.join(__dirname, '../../capability-mock-service'),
        runtime: lambda.Runtime.PYTHON_3_13,
        index: 'app.py',
        handler: 'handler',
        timeout: Duration.seconds(30),
        logRetention: logs.RetentionDays.ONE_WEEK,
        environment: {
          LOG_LEVEL: 'INFO',
          MOCK_CONFIG_TABLE_NAME: mockConfigTable.tableName,
          ORCHESTRATOR_COMMAND_QUEUE_URL: commandQueue.queueUrl, // Give mock access to the reply queue
          VERSION: new Date().toISOString(), // Force redeployment on every run
        },
      });

      // 4. Set the mock Lambda to be triggered by the test SQS queue.
      mockServiceLambda.addEventSource(new SqsEventSource(mockCapabilityQueue));

      // 5. Create an API Gateway to provide a control plane for the tests.
      const api = new apigateway.LambdaRestApi(this, 'MockServiceApi', {
        handler: mockServiceLambda,
        proxy: true,
      });

      // 6. Grant permissions for the mock service.
      mockConfigTable.grantReadWriteData(mockServiceLambda);
      commandQueue.grantSendMessages(mockServiceLambda); // Allow mock to send replies

      // 7. Grant the orchestrator permissions to use the test queue.
      orchestratorLambda.addEnvironment('CCH_MOCK_SQS_URL', mockCapabilityQueue.queueUrl);
      mockCapabilityQueue.grantSendMessages(orchestratorLambda);

      // 8. Output the API Gateway URL for the tests to use.
      new cdk.CfnOutput(this, 'MockServiceApiEndpoint', {
        value: api.url,
        description: 'Control plane endpoint for the mock capability service',
      });
    }

    // Grant the Lambda function permissions to write to the state table
    stateTable.grantReadWriteData(orchestratorLambda);

    if (props?.isTest) {
      const testExecutorUserArn = process.env.TEST_EXECUTOR_USER_ARN;
      if (!testExecutorUserArn) {
        // ... existing code ...
      }
    }
  }
}
