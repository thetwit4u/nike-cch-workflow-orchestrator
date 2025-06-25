#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { CchWorkflowOrchestratorStack } from '../lib/cdk-stack';

// Get profile from command line arguments
const app = new cdk.App();
const env = app.node.tryGetContext('env') || 'dev';
const owner = app.node.tryGetContext('owner') || '';
const profile = app.node.tryGetContext('profile'); // Allow profile to be passed via --profile

// Set AWS credentials profile if provided
if (profile) {
  process.env.AWS_PROFILE = profile;
  console.log(`Using AWS profile: ${profile}`);
  
  // If profile is 'dev', set development environment variables
  if (profile === 'dev') {
    process.env.CDK_ENV = 'dev';
    process.env.CCH_OWNER = '';
    process.env.NIKE_ORG_L3 = 'trade-customs-compliance-hub';
    process.env.NIKE_OWNER = '';
    process.env.NIKE_DL = 'Lst-gt.scpt.tt.trade.all@Nike.com';
  }
}

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