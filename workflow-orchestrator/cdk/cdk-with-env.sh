#!/usr/bin/env zsh
# Helper script to run CDK commands with a specified .env file

# Default environment file
ENV_FILE=".env"

# Parse command-line arguments for a custom env file
if [[ "$1" == "-f" ]] || [[ "$1" == "--file" ]]; then
  if [[ -n "$2" ]]; then
    ENV_FILE="$2"
    shift 2 # Remove -f and filename from arguments
  else
    echo "Error: Missing file name for $1 option." >&2
    exit 1
  fi
fi

# Check if the specified .env file exists
if [ ! -f "$ENV_FILE" ]; then
  echo "Error: Environment file not found at '$ENV_FILE'" >&2
  if [[ "$ENV_FILE" == ".env" ]]; then
      echo "Please create a .env file based on .env.example" >&2
  fi
  exit 1
fi

# Load and export environment variables from the specified file
set -a  # Automatically export all variables
source "$ENV_FILE"
set +a  # Turn off auto-export

# Debug output
echo "Loaded environment variables from $ENV_FILE:"
echo "CCH_OWNER=${CCH_OWNER}"
echo "CDK_ENV=${CDK_ENV}"

# Run the CDK command with the rest of the arguments
echo "Running: npx cdk $@"
env npx cdk "$@"
