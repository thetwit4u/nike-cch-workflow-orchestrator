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
import { Platform } from "aws-cdk-lib/aws-ecr-assets";

interface CchWorkflowOrchestratorStackProps extends StackProps {
    isTestEnv?: boolean; // Use this flag to conditionally create test resources
}

export class CchWorkflowOrchestratorStack extends Stack {
    constructor(scope: Construct, id: string, props?: CchWorkflowOrchestratorStackProps) {
        super(scope, id, props);

        const isTestEnv = props?.isTestEnv ?? (this.node.tryGetContext('env') === 'test' || process.env.CDK_ENV === 'test');
        const env = this.node.tryGetContext('env') || process.env.CDK_ENV || 'dev';
        const owner = this.node.tryGetContext('owner') || process.env.CCH_OWNER || 'userid';
        const orgLevel3 = process.env.NIKE_ORG_L3 || 'trade-customs-compliance-hub';
        const imageUri = this.node.tryGetContext('image_uri');

        const authorizedServicesList = process.env.AUTHORIZED_COMMAND_QUEUE_SENDERS || '';

        const capabilityEnvVars: { [key: string]: string } = {};
        Object.keys(process.env).forEach(key => {
            if (key.startsWith('CCH_CAPABILITY_')) {
                capabilityEnvVars[key] = process.env[key] || '';
            }
        });

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
        const stateTableName = `${mainPrefix}-workflow-state-table-${env}${ownerSuffix}`;
        const eventdataBucketName = `${mainPrefix}-eventdata-bucket-${env}${ownerSuffix}`;
        const definitionsBucketName = `${definitionsBucketPrefix}-${env}${ownerSuffix}`;
        const schedulerGroupName = `${mainPrefix}-schedules-${env}${ownerSuffix}`;
        
        const commandQueue = new sqs.Queue(this, 'OrchestratorCommandQueue', {
            queueName: commandQueueName,
            visibilityTimeout: Duration.seconds(300),
        });

        if (authorizedServicesList) {
            const authorizedServices = authorizedServicesList.split(',').map(s => s.trim()).filter(s => s);
            if (authorizedServices.length > 0) {
                commandQueue.addToResourcePolicy(
                    new iam.PolicyStatement({
                        sid: 'AllowAuthorizedServicesToSendMessages',
                        effect: iam.Effect.ALLOW,
                        principals: authorizedServices.map(arn => new iam.ArnPrincipal(arn)),
                        actions: ['sqs:SendMessage'],
                        resources: [commandQueue.queueArn],
                    }),
                );
            }
        }
        
        const schedulerRole = new iam.Role(this, 'SchedulerRole', {
            assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
        });
        commandQueue.grantSendMessages(schedulerRole);

        new scheduler.CfnScheduleGroup(this, 'SchedulerGroup', {
            name: schedulerGroupName,
        });

        const stateTable = new dynamodb.Table(this, 'WorkflowStateTable', {
            tableName: stateTableName,
            partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
            sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
            billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        const definitionsBucket = new s3.Bucket(this, 'WorkflowDefinitionsBucket', {
            bucketName: definitionsBucketName,
            versioned: true,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            autoDeleteObjects: true,
        });

        const eventdataBucket = new s3.Bucket(this, 'EventDataBucket', {
            bucketName: eventdataBucketName,
            versioned: true,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            autoDeleteObjects: true,
        });
        
        // --- Mock Service (only for test environments) ---
        let mockCapabilityQueue: sqs.Queue | undefined;

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

            const mockServiceLambda = new python.PythonFunction(this, 'CapabilityMockServiceFunction', {
                functionName: `${mainPrefix}-mock-lambda-${env}${ownerSuffix}`,
                entry: path.join(__dirname, '../../capability-mock-service'),
                runtime: lambda.Runtime.PYTHON_3_13,
                index: 'app.py',
                handler: 'handler',
                environment: {
                    MOCK_CONFIG_TABLE_NAME: mockConfigTable.tableName,
                    ORCHESTRATOR_COMMAND_QUEUE_URL: commandQueue.queueUrl,
                    LOG_LEVEL: 'INFO',
                    VERSION: new Date().toISOString(),
                },
                timeout: Duration.seconds(30),
            });

            mockServiceLambda.addEventSource(new SqsEventSource(mockCapabilityQueue));
            mockConfigTable.grantReadWriteData(mockServiceLambda);
            commandQueue.grantSendMessages(mockServiceLambda);
            eventdataBucket.grantRead(mockServiceLambda);

            new logs.LogRetention(this, 'MockServiceLogRetention', {
                logGroupName: mockServiceLambda.logGroup.logGroupName,
                retention: logs.RetentionDays.ONE_DAY,
            });
            
            new cdk.CfnOutput(this, 'MockConfigTableName', {
                value: mockConfigTable.tableName,
            });
            new cdk.CfnOutput(this, 'MockCapabilityQueueUrl', {
                value: mockCapabilityQueue.queueUrl,
            });

            const testExecutorArn = process.env.TEST_EXECUTOR_ARN;
            if (testExecutorArn) {
                const testExecutorRole = new iam.Role(this, 'TestExecutorRole', {
                    roleName: `${mainPrefix}-test-executor-role-${env}${ownerSuffix}`,
                    assumedBy: new iam.ArnPrincipal(testExecutorArn),
                    description: 'Role for BDD test execution, providing access to stack resources.',
                });

                testExecutorRole.assumeRolePolicy?.addStatements(
                    new iam.PolicyStatement({
                        actions: ['sts:TagSession'],
                        principals: [new iam.ArnPrincipal(testExecutorArn)],
                        effect: iam.Effect.ALLOW,
                    })
                );

                commandQueue.grantSendMessages(testExecutorRole);
                stateTable.grantReadData(testExecutorRole);
                definitionsBucket.grantReadWrite(testExecutorRole);
                eventdataBucket.grantReadWrite(testExecutorRole);
                mockCapabilityQueue.grantSendMessages(testExecutorRole);
                mockConfigTable.grantReadWriteData(testExecutorRole);

                new cdk.CfnOutput(this, 'TestExecutorRoleArn', {
                    value: testExecutorRole.roleArn,
                    description: 'ARN of the IAM role for the BDD test executor'
                });
            }
        }

        const commonLambdaEnv = {
            STATE_TABLE_NAME: stateTable.tableName,
            DEFINITIONS_BUCKET_NAME: definitionsBucket.bucketName,
            COMMAND_QUEUE_URL: commandQueue.queueUrl,
            SCHEDULER_ROLE_ARN: schedulerRole.roleArn,
            SCHEDULER_GROUP_NAME: schedulerGroupName,
            LOG_LEVEL: 'INFO',
            ...capabilityEnvVars,
            VERSION: new Date().toISOString(),
        };

        let orchestratorLambda;

        if (imageUri) {
            // Pipeline deployment: Use the ECR image
            const repository = ecr.Repository.fromRepositoryArn(this, 'EcrRepository', `arn:aws:ecr:${this.region}:${this.account}:repository/${mainPrefix}`);
            orchestratorLambda = new lambda.DockerImageFunction(this, 'OrchestratorLambda', {
                functionName: `${mainPrefix}-lambda-${env}${ownerSuffix}`,
                code: lambda.DockerImageCode.fromEcr(repository, {
                    tagOrDigest: imageUri,
                }),
                memorySize: 1024,
                environment: commonLambdaEnv,
                timeout: Duration.seconds(300),
            });
        } else {
            // Local deployment: Build from source
            orchestratorLambda = new python.PythonFunction(this, 'OrchestratorLambda', {
                functionName: `${mainPrefix}-lambda-${env}${ownerSuffix}`,
                entry: path.join(__dirname, '../../src'),
                runtime: lambda.Runtime.PYTHON_3_13,
                index: 'app.py',
                handler: 'handler',
                memorySize: 1024,
                environment: commonLambdaEnv,
                timeout: Duration.seconds(300),
                bundling: {
                    assetExcludes: ['.DS_Store', '.venv', 'tests'],
                },
            });
        }

        orchestratorLambda.addEventSource(new SqsEventSource(commandQueue));
        definitionsBucket.grantRead(orchestratorLambda);
        eventdataBucket.grantReadWrite(orchestratorLambda);
        stateTable.grantReadWriteData(orchestratorLambda);
        
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
        
        Object.values(capabilityEnvVars).forEach((queueUrl) => {
            if (mockCapabilityQueue && queueUrl === mockCapabilityQueue.queueUrl) {
                return;
            }
            const queue = sqs.Queue.fromQueueArn(this, `CapabilityQueue-${queueUrl}`, cdk.Fn.importValue(queueUrl));
            if (queue) {
               (orchestratorLambda.role as iam.Role).addToPrincipalPolicy(
                    new iam.PolicyStatement({
                        actions: ['sqs:SendMessage'],
                        resources: [queue.queueArn],
                    }),
                );
            }
        });

        new logs.LogRetention(this, 'OrchestratorLogRetention', {
            logGroupName: orchestratorLambda.logGroup.logGroupName,
            retention: logs.RetentionDays.ONE_WEEK,
        });

        new cdk.CfnOutput(this, 'OrchestratorCommandQueueUrl', {
            value: commandQueue.queueUrl,
            description: 'The URL of the central command queue for the orchestrator.',
        });
        new cdk.CfnOutput(this, 'WorkflowStateTableName', {
            value: stateTable.tableName,
            description: 'The name of the DynamoDB table for storing workflow state.',
        });
        new cdk.CfnOutput(this, 'DefinitionsBucketName', {
            value: definitionsBucket.bucketName,
            description: 'The name of the S3 bucket for storing workflow definitions.',
        });
        new cdk.CfnOutput(this, 'IngestBucketName', {
            value: eventdataBucket.bucketName,
            description: 'The name of the S3 bucket for ingesting event data.',
        });
    }
}
