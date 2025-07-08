import subprocess
import os
from pathlib import Path
import logging
import argparse

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EnvironmentManager:
    """
    Manages the deployment and destruction of the AWS CDK test stack.

    This class provides a programmatic interface to run AWS CDK commands
    to create and tear down the necessary infrastructure for running
    end-to-end integration tests.
    """

    def __init__(self, cdk_dir: str = "workflow-orchestrator/cdk"):
        """
        Initializes the TestEnvironmentManager.

        Args:
            cdk_dir: The relative path to the directory containing the CDK app.
        """
        self.cdk_dir = Path(cdk_dir).resolve()
        if not self.cdk_dir.is_dir():
            raise FileNotFoundError(f"CDK directory not found at: {self.cdk_dir}")
        self.output_file = self.cdk_dir / "cdk-outputs.json"

    def _run_command(self, command: list[str]):
        """
        Executes a shell command in the CDK directory.

        Args:
            command: A list of strings representing the command and its arguments.

        Returns:
            The stdout from the completed process.

        Raises:
            subprocess.CalledProcessError: If the command returns a non-zero exit code.
        """
        logger.info(f"Running command: {' '.join(command)} in {self.cdk_dir}")
        try:
            # Using os.environ to pass through the current environment,
            # which is necessary for AWS credentials and other CDK contexts.
            process = subprocess.run(
                command,
                cwd=self.cdk_dir,
                capture_output=True,
                text=True,
                check=True,
                env=os.environ
            )
            logger.info("Command successful.")
            if process.stdout:
                logger.info("STDOUT:\n%s", process.stdout)
            if process.stderr:
                logger.warning("STDERR:\n%s", process.stderr)
            return process.stdout
        except subprocess.CalledProcessError as e:
            logger.error(f"Error executing command: {' '.join(command)}")
            logger.error("STDOUT:\n%s", e.stdout)
            logger.error("STDERR:\n%s", e.stderr)
            raise e

    def deploy(self, profile: str = None):
        """
        Deploys the CDK stack and captures the outputs to a JSON file.

        The '--require-approval never' flag is used for non-interactive deployment.
        The '--outputs-file' flag directs the CDK to write stack outputs to a
        specified file, which is crucial for configuring tests dynamically.
        """
        logger.info("Deploying CDK stack for BDD tests...")
        
        command = [
            "./cdk-with-env.sh",
            "-f",
            ".env.bdd",
            "deploy",
            "--all",
            "--require-approval",
            "never",
            "--outputs-file",
            str(self.output_file),
            # Add test context to ensure test-only resources are created
            "-c",
            "test=true"
        ]
        if profile:
            command.extend(["--profile", profile])
            
        self._run_command(command)
        logger.info(f"CDK stack deployed. Outputs saved to {self.output_file}")

    def destroy(self, profile: str = None):
        """
        Destroys the CDK stack.

        The '--force' flag is used to bypass the confirmation prompt, which is
        necessary for automated teardown.
        """
        logger.info("Destroying CDK stack for BDD tests...")

        command = [
            "./cdk-with-env.sh",
            "-f",
            ".env.bdd",
            "destroy",
            "--all",
            "--force",
        ]
        if profile:
            command.extend(["--profile", profile])
            
        self._run_command(command)
        logger.info("CDK stack destroyed successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage the BDD test environment.")
    parser.add_argument("action", choices=["deploy", "destroy"], help="The action to perform.")
    parser.add_argument("--profile", help="The AWS profile to use for the command.")
    args = parser.parse_args()

    manager = EnvironmentManager()

    if args.action == "deploy":
        manager.deploy(profile=args.profile)
    elif args.action == "destroy":
        manager.destroy(profile=args.profile) 