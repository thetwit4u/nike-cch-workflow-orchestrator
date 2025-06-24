#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { CchWorkflowOrchestratorStack } from '../lib/cdk-stack';

const app = new cdk.App();
const env = app.node.tryGetContext('env') || 'dev';
const owner = app.node.tryGetContext('owner') || 'userid';
new CchWorkflowOrchestratorStack(app, `CchWorkflowOrchestratorStack-${env}-${owner}`, {
  /* If you don't specify 'env', this stack will be environment-agnostic.
   * Account/Region-dependent features and context lookups will not work,
   * but a single synthesized template can be deployed anywhere. */

  /* Uncomment the next line to specialize this stack for the AWS Account
   * and Region that are implied by the current CLI configuration. */
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'eu-west-1' },

  /* Uncomment the next line if you know exactly what Account and Region you
   * want to deploy the stack to. */
  // env: { account: '123456789012', region: 'us-east-1' },

  /* For more information, see https://docs.aws.amazon.com/cdk/latest/guide/environments.html */
});