// Example code to allow external Lambda access to command queue
// Add this to your CDK stack in cdk-stack.ts

// Grant access to a Lambda from another project
const externalLambdaArn = 'arn:aws:lambda:eu-west-1:EXTERNAL_ACCOUNT_ID:function:external-lambda-function-name';
const externalLambdaRole = 'arn:aws:iam::EXTERNAL_ACCOUNT_ID:role/external-lambda-execution-role';

// Method 1: Add a resource policy to the SQS queue
commandQueue.addToResourcePolicy(
  new iam.PolicyStatement({
    effect: iam.Effect.ALLOW,
    principals: [
      new iam.ArnPrincipal(externalLambdaRole)
    ],
    actions: [
      'sqs:SendMessage',
      'sqs:ReceiveMessage',
      'sqs:DeleteMessage',
      'sqs:GetQueueAttributes'
    ],
    resources: [commandQueue.queueArn]
  })
);

// Method 2: Create a specific access policy (alternative)
// This could be provided to the external account
const accessPolicy = new iam.PolicyDocument({
  statements: [
    new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'sqs:SendMessage',
        'sqs:ReceiveMessage',
        'sqs:DeleteMessage',
        'sqs:GetQueueAttributes'
      ],
      resources: [commandQueue.queueArn]
    })
  ]
});

// Print the policy JSON that can be shared with external team
new cdk.CfnOutput(this, 'ExternalAccessPolicy', {
  value: JSON.stringify(accessPolicy.toJSON(), null, 2),
  description: 'Policy document that can be attached to the external Lambda role'
});
