import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import {
    aws_sqs as sqs,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_lambda as lambda,
    aws_logs as logs,
    Duration,
    Stack,
    StackProps,
    Tags,
    aws_iam as iam,
    aws_scheduler as scheduler,
    aws_ecr as ecr
} from 'aws-cdk-lib';
import * as python from '@aws-cdk/aws-lambda-python-alpha';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import * as path from 'path';
import {ISecurityGroup, ISubnet, SecurityGroup, Subnet, Vpc} from "aws-cdk-lib/aws-ec2";


interface CchWorkflowOrchestratorStackProps extends StackProps {
    isTestEnv?: boolean;
}

export class CchWorkflowOrchestratorStack extends Stack {
    constructor(scope: Construct, id: string, props?: CchWorkflowOrchestratorStackProps) {
        super(scope, id, props);

        // --- Context and Environment Variables ---
        const env = this.node.tryGetContext('env') || process.env.ENVIRONMENT || 'st';
        const owner = this.node.tryGetContext('owner') || process.env.CCH_OWNER || 'userid';
        const isTestEnv = props?.isTestEnv ?? (this.node.tryGetContext('test') === 'true');
        const authorizedServicesList = process.env.AUTHORIZED_COMMAND_QUEUE_SENDERS || '';
        const testExecutorArn = process.env.TEST_EXECUTOR_ARN;

        const capabilityEnvVars: { [key: string]: string } = {};
        Object.keys(process.env).forEach(key => {
            if (key.startsWith('CCH_CAPABILITY_')) {
                capabilityEnvVars[key] = process.env[key] || '';
            }
        });

        // --- Tags ---
        Tags.of(this).add('nike-tagguid', `${process.env.NIKE_TAGGUID}`);
        this.templateOptions.description = "Stack for the CCH Workflow Orchestrator";

        // --- Resource Naming ---
        const ownerSuffix = owner ? `-${owner}` : '';
        const mainPrefix = 'cch-flow-orchestrator';
        const definitionsBucketPrefix = 'cch-flow-definitions';
        const platformType = 'core';
        const dataClassification = 'ru';

        // Resolve VPC from vpcId
        const vpc = Vpc.fromLookup(this, 'Vpc', { vpcId: (process.env.VPC_ID || '') });

        // Resolve subnets from subnetIds
        const subnets: ISubnet[] = (process.env.NON_ROUTABLE_SUBNETS?.split(',') || []).map((subnetId, idx) =>
            Subnet.fromSubnetId(this, `Subnet${idx}`, subnetId)
        );

        // Resolve security groups from securityGroupIds
        const securityGroups: ISecurityGroup[] = ([process.env.DEFAULT_SECURITY_GROUP || '']).map((sgId, idx) =>
            SecurityGroup.fromSecurityGroupId(this, `SG${idx}`, sgId)
        );

        const logGroup = new logs.LogGroup(this, 'LogGroup', {
            logGroupName: `/opentelemetry/${platformType}-${dataClassification}/aws/lambda/${process.env.SERVICE_NAME || ''}`,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            retention: logs.RetentionDays.ONE_WEEK
        });

        const role = new iam.Role(this, 'LambdaExecutionRole', {
            assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
                iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaVPCAccessExecutionRole')
            ],
            roleName: `${mainPrefix}-${env}-${cdk.Aws.REGION}`
        });

        // --- SQS Queues ---
        const commandQueue = new sqs.Queue(this, 'OrchestratorCommandQueue', {
            queueName: `${mainPrefix}-command-queue-${env}${ownerSuffix}`,
            visibilityTimeout: Duration.seconds(300),
        });

        const replyQueue = new sqs.Queue(this, 'OrchestratorReplyQueue', {
            queueName: `${mainPrefix}-reply-queue-${env}${ownerSuffix}`,
            visibilityTimeout: Duration.seconds(300),
        });

        const importRequestQueue = new sqs.Queue(this, 'ImportRequestQueue', {
            queueName: `${mainPrefix}-capability-import-request-queue-${env}${ownerSuffix}`,
            visibilityTimeout: Duration.seconds(300),
        });

        const exportRequestQueue = new sqs.Queue(this, 'ExportRequestQueue', {
            queueName: `${mainPrefix}-capability-export-request-queue-${env}${ownerSuffix}`,
            visibilityTimeout: Duration.seconds(300),
        });

        // --- DynamoDB State Table ---
        const stateTable = new dynamodb.Table(this, 'WorkflowStateTable', {
            tableName: `${mainPrefix}-workflow-state-table-${env}${ownerSuffix}`,
            partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
            sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
            billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        // --- S3 Buckets ---
        const definitionsBucket = new s3.Bucket(this, 'WorkflowDefinitionsBucket', {
            bucketName: `${definitionsBucketPrefix}-${env}${ownerSuffix}`,
            versioned: true,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            autoDeleteObjects: true,
        });

        const eventdataBucket = new s3.Bucket(this, 'EventDataBucket', {
            bucketName: `${mainPrefix}-eventdata-bucket-${env}${ownerSuffix}`,
            versioned: true,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            autoDeleteObjects: true,
        });

        // --- EventBridge Scheduler ---
        const schedulerGroupName = `${mainPrefix}-schedules-${env}${ownerSuffix}`;
        const schedulerRole = new iam.Role(this, 'SchedulerRole', {
            assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
        });
        commandQueue.grantSendMessages(schedulerRole);
        new scheduler.CfnScheduleGroup(this, 'SchedulerGroup', {
            name: schedulerGroupName,
        });

        // --- Test Environment Specific Resources ---
        let mockCapabilityQueue: sqs.Queue | undefined;
        let mockServiceLambda: python.PythonFunction | undefined;
        if (isTestEnv) {
            const mockQueueName = `${mainPrefix}-mock-capability-queue-${env}${ownerSuffix}`;
            const mockConfigTableName = `${mainPrefix}-mock-config-table-${env}${ownerSuffix}`;

            const mockConfigTable = new dynamodb.Table(this, 'MockConfigTable', {
                tableName: mockConfigTableName,
                partitionKey: { name: 'capability_id', type: dynamodb.AttributeType.STRING },
                billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            });

            mockCapabilityQueue = new sqs.Queue(this, 'MockCapabilityQueue', {
                queueName: mockQueueName,
                visibilityTimeout: Duration.seconds(300),
            });
            capabilityEnvVars['CCH_CAPABILITY_IMPORT'] = mockCapabilityQueue.queueUrl;

            mockServiceLambda = new python.PythonFunction(this, 'CapabilityMockServiceFunction', {
                functionName: `${mainPrefix}-mock-lambda-${env}${ownerSuffix}`,
                entry: path.join(__dirname, '../../capability-mock-service'),
                runtime: lambda.Runtime.PYTHON_3_13,
                index: 'app.py',
                handler: 'handler',
                environment: {
                    MOCK_CONFIG_TABLE_NAME: mockConfigTable.tableName,
                    ORCHESTRATOR_COMMAND_QUEUE_URL: commandQueue.queueUrl,
                    REPLY_QUEUE_URL: replyQueue.queueUrl, // Mock also sends to reply queue
                    LOG_LEVEL: 'INFO',
                    VERSION: new Date().toISOString(),
                },
                timeout: Duration.seconds(30),
            });
            mockServiceLambda.addEventSource(new SqsEventSource(mockCapabilityQueue)); // Test mock listens to its dedicated queue
            mockConfigTable.grantReadWriteData(mockServiceLambda);
            commandQueue.grantSendMessages(mockServiceLambda);
            replyQueue.grantSendMessages(mockServiceLambda);
            eventdataBucket.grantRead(mockServiceLambda);

            new cdk.CfnOutput(this, 'MockConfigTableName', { value: mockConfigTable.tableName });
            new cdk.CfnOutput(this, 'MockCapabilityQueueUrl', { value: mockCapabilityQueue.queueUrl });

            if (testExecutorArn) {
                const testExecutorRole = new iam.Role(this, 'TestExecutorRole', {
                    roleName: `${mainPrefix}-test-executor-role-${env}${ownerSuffix}`,
                    assumedBy: new iam.ArnPrincipal(testExecutorArn),
                });
                testExecutorRole.assumeRolePolicy?.addStatements(new iam.PolicyStatement({
                    actions: ['sts:TagSession'],
                    principals: [new iam.ArnPrincipal(testExecutorArn)],
                }));
                commandQueue.grantSendMessages(testExecutorRole);
                stateTable.grantReadData(testExecutorRole);
                definitionsBucket.grantReadWrite(testExecutorRole);
                eventdataBucket.grantReadWrite(testExecutorRole);
                mockCapabilityQueue.grantSendMessages(testExecutorRole);
                mockConfigTable.grantReadWriteData(testExecutorRole);
                new cdk.CfnOutput(this, 'TestExecutorRoleArn', { value: testExecutorRole.roleArn });
            }
        }

        // --- Orchestrator Lambda Function (Conditional Build) ---
        const commonLambdaEnv = {
            STATE_TABLE_NAME: stateTable.tableName,
            DEFINITIONS_BUCKET_NAME: definitionsBucket.bucketName,
            COMMAND_QUEUE_URL: commandQueue.queueUrl,
            COMMAND_QUEUE_ARN: commandQueue.queueArn,
            REPLY_QUEUE_URL: replyQueue.queueUrl,
            IMPORT_QUEUE_URL: importRequestQueue.queueUrl,
            EXPORT_QUEUE_URL: exportRequestQueue.queueUrl,
            SCHEDULER_ROLE_ARN: schedulerRole.roleArn,
            SCHEDULER_GROUP_NAME: schedulerGroupName,
            DISABLE_OPENTELEMETRY: process.env.DISABLE_OPENTELEMETRY || 'false',
            LOG_LEVEL: 'INFO',
            ...capabilityEnvVars,
            VERSION: new Date().toISOString(),
            OTEL_EXPORTER_OTLP_ENDPOINT: `https://trade-${process.env.ENVIRONMENT || 'st'}-otel-${cdk.Aws.REGION}.${process.env.HOSTED_ZONE_NAME || ''}:4318`,
            OTEL_SERVICE_NAME: process.env.SERVICE_NAME || '',
            OTEL_EXPORTER_OTLP_PROTOCOL: 'http/protobuf',
            OTEL_LOGS_EXPORTER: 'otlp',
            OTEL_METRICS_EXPORTER: 'none',
            OTEL_TRACES_EXPORTER: 'otlp',
            OTEL_PROPAGATORS: 'tracecontext'
        };

        let orchestratorLambda: lambda.Function;
        if (process.env.SERVICE_NAME && process.env.SERVICE_VERSION) {
            const repository = ecr.Repository.fromRepositoryName(this, "EcrRepository", process.env.SERVICE_NAME || '');
            orchestratorLambda = new lambda.DockerImageFunction(this, 'OrchestratorLambda', {
                functionName: `${mainPrefix}-lambda-${env}${ownerSuffix}`,
                code: lambda.DockerImageCode.fromEcr(repository, { tagOrDigest: process.env.SERVICE_VERSION || ''}),
                role: role,
                vpc,
                vpcSubnets: { subnets },
                securityGroups,
                logGroup: logGroup,
                memorySize: 1024,
                environment: commonLambdaEnv,
                timeout: Duration.seconds(300)
            });
        } else {
            orchestratorLambda = new python.PythonFunction(this, 'OrchestratorLambda', {
                functionName: `${mainPrefix}-lambda-${env}${ownerSuffix}`,
                entry: path.join(__dirname, '../../src'),
                runtime: lambda.Runtime.PYTHON_3_13,
                role: role,
                logGroup: logGroup,
                index: 'app.py',
                handler: 'handler',
                memorySize: 1024,
                environment: commonLambdaEnv,
                timeout: Duration.seconds(300),
                bundling: {
                    command: [
                        'bash', '-c',
                        'rsync -av --exclude="*.pyc" --exclude="__pycache__" . /asset-output/ && pip install -r /asset-output/requirements.txt -t /asset-output'
                    ]
                },
            });
        }

        // --- Permissions ---
        orchestratorLambda.addEventSource(new SqsEventSource(commandQueue));
        orchestratorLambda.addEventSource(new SqsEventSource(replyQueue));
        definitionsBucket.grantRead(orchestratorLambda);
        eventdataBucket.grantReadWrite(orchestratorLambda);
        stateTable.grantReadWriteData(orchestratorLambda);
        importRequestQueue.grantSendMessages(orchestratorLambda);
        exportRequestQueue.grantSendMessages(orchestratorLambda);
        if (mockCapabilityQueue) {
            mockCapabilityQueue.grantSendMessages(orchestratorLambda);
        }
        orchestratorLambda.addToRolePolicy(new iam.PolicyStatement({
            actions: ['iam:PassRole'],
            resources: [schedulerRole.roleArn],
        }));
        orchestratorLambda.addToRolePolicy(new iam.PolicyStatement({
            actions: ['scheduler:CreateSchedule', 'scheduler:UpdateSchedule', 'scheduler:DeleteSchedule', 'scheduler:GetSchedule'],
            resources: [`arn:aws:scheduler:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:schedule/${schedulerGroupName}/*`],
        }));
        if (authorizedServicesList) {
            const authorizedServices = authorizedServicesList.split(',').map(s => s.trim()).filter(s => s);
            if (authorizedServices.length > 0) {
                commandQueue.addToResourcePolicy(new iam.PolicyStatement({
                    sid: 'AllowAuthorizedServicesToSendMessages',
                    effect: iam.Effect.ALLOW,
                    principals: authorizedServices.map(arn => new iam.ArnPrincipal(arn)),
                    actions: ['sqs:SendMessage'],
                    resources: [commandQueue.queueArn],
                }));
            }
        }

        // --- Log Retention & Outputs ---
        new logs.LogRetention(this, 'OrchestratorLogRetention', {
            logGroupName: orchestratorLambda.logGroup.logGroupName,
            retention: logs.RetentionDays.ONE_WEEK,
        });
        if (mockServiceLambda) {
            new logs.LogRetention(this, 'MockServiceLogRetention', {
                logGroupName: mockServiceLambda.logGroup.logGroupName,
                retention: logs.RetentionDays.ONE_DAY,
            });
        }
        new cdk.CfnOutput(this, 'OrchestratorCommandQueueUrl', { value: commandQueue.queueUrl });
        new cdk.CfnOutput(this, 'WorkflowStateTableName', { value: stateTable.tableName });
        new cdk.CfnOutput(this, 'DefinitionsBucketName', { value: definitionsBucket.bucketName });
        new cdk.CfnOutput(this, 'IngestBucketName', { value: eventdataBucket.bucketName });
    }
}