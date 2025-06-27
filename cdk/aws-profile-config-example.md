# AWS Configuration for CCH Flow Orchestrator

This document provides instructions on setting up configurations for the CCH Flow Orchestrator CDK deployment using either AWS profiles or environment variables.

## Option 1: Using .env Files (Recommended)

You can use a `.env` file to set all required environment variables:

1. Create a `.env` file in the `workflow-orchestrator/cdk` directory:

```bash
# AWS credentials (alternative to AWS profiles)
AWS_ACCESS_KEY_ID=YOUR_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY=YOUR_SECRET_ACCESS_KEY
AWS_SESSION_TOKEN=YOUR_SESSION_TOKEN  # Optional, only if using temporary credentials
AWS_REGION=eu-west-1

# CDK and application variables
CDK_DEFAULT_ACCOUNT=123456789012
CDK_ENV=dev
CCH_OWNER=david
NIKE_ORG_L3=trade-customs-compliance-hub
NIKE_OWNER=david.vanheeswijck@nike.com
NIKE_DL=Lst-gt.scpt.tt.trade.all@Nike.com
```

2. Source the file before running CDK commands:

```bash
# Source the .env file and run CDK
source .env && npx cdk deploy
```

3. Or use a tool like `dotenv-cli`:

```bash
# Install dotenv-cli
npm install -g dotenv-cli

# Run CDK with .env file
dotenv -e .env -- npx cdk deploy
```

## Option 2: Using AWS Profiles

Alternatively, you can set up AWS profiles:

### 1. `~/.aws/credentials`

```ini
[dev]
aws_access_key_id = YOUR_ACCESS_KEY_ID
aws_secret_access_key = YOUR_SECRET_ACCESS_KEY
aws_session_token = YOUR_SESSION_TOKEN  # Optional, only if using temporary credentials
```

### 2. `~/.aws/config`

```ini
[profile dev]
region = eu-west-1
output = json
# Optional settings for assuming a role
# role_arn = arn:aws:iam::ACCOUNT_ID:role/ROLE_NAME
# source_profile = default
```

### Environment Variables

When using the `--context profile=dev` parameter, the CDK deployment will automatically set the following environment variables:

| Variable | Value | Description |
|----------|-------|-------------|
| CDK_ENV | dev | Environment name |
| CCH_OWNER | david | Owner identifier |
| NIKE_ORG_L3 | trade-customs-compliance-hub | Organization level 3 |
| NIKE_OWNER | david.vanheeswijck@nike.com | Owner email |
| NIKE_DL | Lst-gt.scpt.tt.trade.all@Nike.com | Distribution list |

### Usage

To deploy the stack using the configured profile:

```bash
# For development environment
npx cdk deploy --context profile=dev

# For synthesis (to check what will be deployed)
npx cdk synth --context profile=dev
```

## Custom Environment Variables (Optional)

If you need to customize environment variables beyond what's automatically set, create a `.env` file:

```bash
# .env
CDK_DEFAULT_ACCOUNT=123456789012
CDK_ENV=dev
CCH_OWNER=david
NIKE_ORG_L3=trade-customs-compliance-hub
NIKE_OWNER=david.van@nike.com
NIKE_DL=Lst-gt.scpt.tt.trade.all@Nike.com
```

Then source it before running CDK:

```bash
source .env && npx cdk deploy --profile dev
```
