#!/usr/bin/env zsh
# Helper script to run CDK commands with .env file

# Check if .env file exists
if [ ! -f .env ]; then
  echo "Error: .env file not found"
  echo "Please create a .env file based on .env.example"
  exit 1
fi

# Load and export environment variables from .env file
set -a  # Automatically export all variables
source .env
set +a  # Turn off auto-export

# Debug output
echo "Loaded environment variables from .env:"
echo "CCH_OWNER=${CCH_OWNER}"
echo "ENVIRONMENT=${ENVIRONMENT}"

# Run the CDK command with all arguments passed to this script
# Use env to explicitly pass environment variables to the Node.js process
env npx cdk "$@"
