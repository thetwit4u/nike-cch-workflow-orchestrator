import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { aws_sqs as sqs, aws_dynamodb as dynamodb, aws_s3 as s3, aws_lambda as lambda, aws_logs as logs, Duration, Stack, StackProps, Tags } from 'aws-cdk-lib';
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

    Tags.of(this).add('nike-owner', process.env.NIKE_OWNER || 'stijn.liesenborghs@nike.com');
    Tags.of(this).add('nike-distributionlist', process.env.NIKE_DL || 'Lst-gt.scpt.tt.trade.all@Nike.com');
    Tags.of(this).add('nike-environment', env);
    Tags.of(this).add('nike-org-level1', 'scpt');
    Tags.of(this).add('nike-org-level2', 'trade-transportation');
    Tags.of(this).add('nike-org-level3', orgLevel3);
    Tags.of(this).add('nike-owner-id', owner);

    this.templateOptions.description = "Stack for the CCH Workflow Orchestrator PoC";

    const prefix = 'cch';

    const commandQueueName = `${prefix}-${env}-${owner}-orchestrator-command-queue`;
    const replyQueueName = `${prefix}-${env}-${owner}-orchestrator-reply-queue`;
    const importRequestQueueName = `${prefix}-${env}-${owner}-capability-import-request-queue`;
    const exportRequestQueueName = `${prefix}-${env}-${owner}-capability-export-request-queue`;
    const stateTableName = `${prefix}-${env}-${owner}-workflow-state-table`;
    const definitionsBucketName = `${prefix}-${env}-${owner}-workflow-definitions-bucket`;
    const eventdataBucketName = `${prefix}-${env}-${owner}-eventdata-bucket`;

    // SQS Queues
    const commandQueue = new sqs.Queue(this, 'OrchestratorCommandQueue', {
      queueName: commandQueueName,
      visibilityTimeout: Duration.seconds(300),
    });

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
        REPLY_QUEUE_URL: replyQueue.queueUrl,
        IMPORT_QUEUE_URL: importRequestQueue.queueUrl,
        EXPORT_QUEUE_URL: exportRequestQueue.queueUrl,
        VERSION: new Date().toISOString(), // Force code update
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
    stateTable.grantReadWriteData(orchestratorLambda);
    definitionsBucket.grantRead(orchestratorLambda);
    eventdataBucket.grantRead(orchestratorLambda);

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
  }
}
