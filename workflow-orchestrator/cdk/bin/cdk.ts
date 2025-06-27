#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { CchWorkflowOrchestratorStack } from '../lib/cdk-stack';

// Get profile from command line arguments
const app = new cdk.App();
const env = app.node.tryGetContext('env') || process.env.ENVIRONMENT || 'dev';
const owner = app.node.tryGetContext('owner') || process.env.CCH_OWNER || '';
const ownerSuffix = owner ? `-${owner}` : '';
const profile = app.node.tryGetContext('profile'); // Allow profile to be passed via --profile

// Debug logging to verify environment variables
console.log(`Environment: ${env}`);
console.log(`Owner from env: ${process.env.CCH_OWNER}`);
console.log(`Owner used: ${owner}`);
console.log(`Owner suffix: ${ownerSuffix}`);

// Set AWS credentials profile if provided
if (profile) {
  process.env.AWS_PROFILE = profile;
  console.log(`Using AWS profile: ${profile}`);
  
  // If profile is 'dev', set development environment variables only if not already set
  if (profile === 'dev') {
    // Only set if not already defined in environment
    if (!process.env.CDK_ENV) process.env.CDK_ENV = 'dev';
    if (!process.env.NIKE_ORG_L3) process.env.NIKE_ORG_L3 = 'trade-customs-compliance-hub';
    if (!process.env.NIKE_DL) process.env.NIKE_DL = 'Lst-gt.scpt.tt.trade.all@Nike.com';
    
    // Note: Not setting CCH_OWNER or NIKE_OWNER, as these should come from .env
  }
}

new CchWorkflowOrchestratorStack(app, `CchWorkflowOrchestratorStack-${env}${ownerSuffix}`, {
  /* If you don't specify 'env', this stack will be environment-agnostic.
   * Account/Region-dependent features and context lookups will not work,
   * but a single synthesized template can be deployed anywhere. */

  /* Uncomment the next line to specialize this stack for the AWS Account
   * and Region that are implied by the current CLI configuration. */
  // env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'eu-west-1' },

  /* Uncomment the next line if you know exactly what Account and Region you
   * want to deploy the stack to. */
  // env: { account: '123456789012', region: 'us-east-1' },

  /* For more information, see https://docs.aws.amazon.com/cdk/latest/guide/environments.html */
});