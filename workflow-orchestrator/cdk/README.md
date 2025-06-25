# Welcome to your CDK TypeScript project

This is a blank project for CDK development with TypeScript.

The `cdk.json` file tells the CDK Toolkit how to execute your app.

## Setup Options

### Option 1: Using .env Files

1. Create a `.env` file based on `.env.example`
2. Run CDK using the helper script:
   ```bash
   ./cdk-with-env.sh deploy
   ```

   Or source the .env file manually:
   ```bash
   source .env && npx cdk deploy
   ```

### Option 2: Using AWS Profiles

Run CDK with a specific AWS profile:
```bash
npx cdk deploy --context profile=dev
```

See `aws-profile-config-example.md` for detailed instructions.

## Useful commands

* `npm run build`   compile typescript to js
* `npm run watch`   watch for changes and compile
* `npm run test`    perform the jest unit tests
* `npx cdk deploy`  deploy this stack to your default AWS account/region
* `npx cdk diff`    compare deployed stack with current state
* `npx cdk synth`   emits the synthesized CloudFormation template
