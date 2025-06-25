#!/usr/bin/env zsh
# Helper script to run CDK commands with .env file

# Check if .env file exists
if [ ! -f .env ]; then
  echo "Error: .env file not found"
  echo "Please create a .env file based on .env.example"
  exit 1
fi

# Load environment variables from .env file
source .env

# Run the CDK command with all arguments passed to this script
npx cdk "$@"
