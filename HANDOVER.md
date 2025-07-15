# Project Handover: CCH Workflow Orchestrator Testing

**Date:** 2024-08-01

This document provides a complete handover of the current testing environment for the CCH Workflow Orchestrator. It is intended for developers and QA engineers who need to resume testing in a new environment.

## 1. Project Overview

The CCH Workflow Orchestrator is a service designed to execute complex business workflows defined in YAML files. It is built using Python, AWS Lambda, SQS, DynamoDB, and the LangGraph library for state management. The core goal is to provide a scalable and observable system for running automated processes that interact with various internal "capabilities".

The implementation plan and architectural details can be found in `PLANNING.md`.

## 2. Current Status & Recent Changes

The project has recently undergone a significant refactoring of its testing strategy. A suite of old, granular test files has been deprecated and replaced by a more robust, end-to-end integration testing approach.

-   **DELETED:** Numerous `test_*.py` files (e.g., `test_simplified_import_workflow.py`, `test_idempotency_guard_clause.py`).
-   **ADDED:** Two primary integration test files that cover complex scenarios:
    -   `tests/test_merged_simplified_workflow.py`: Tests a streamlined "happy path" and a basic error-handling path for the main import workflow.
    -   `tests/test_hitl_error_handling_workflow.py`: Tests an advanced error-handling scenario where the workflow pauses for "Human-in-the-Loop" (HITL) intervention and can be resumed via an external command.

This new approach provides a more realistic validation of the system's behavior.

## 3. Key Components

### Workflow Definitions

-   **Location:** `/workflow-definitions/`
-   **Key Files:**
    -   `import_us_v1.1.1-simplified.yaml`: A streamlined workflow definition used for happy path testing.
    -   `import_us_v1.1.1-simplified-errorhandling.yaml`: A more complex definition that includes conditional error handling and a "wait" state for HITL scenarios.

### Testing Framework

-   **Framework:** `pytest`
-   **Test Location:** `tests/`
-   **Core Logic:** The tests work by:
    1.  Using a deployed AWS stack (via CDK).
    2.  Reading resource names (e.g., SQS queues, S3 buckets) from a `cdk-outputs.json` file.
    3.  Uploading test data and workflow definitions to S3.
    4.  Sending a "start command" message to an SQS queue to trigger the orchestrator Lambda.
    5.  Using a `WorkflowVerifier` utility (`tests/utils/workflow_verifier.py`) to poll a DynamoDB table and assert the final state of the workflow run.

## 4. Environment Setup for Testing

To run the integration tests, you must have a deployed instance of the orchestrator stack.

### Prerequisites

1.  **AWS Account & Credentials:** Your local environment must be configured with AWS credentials that have permission to deploy the CDK stack and interact with S3, SQS, and DynamoDB.
2.  **Python 3.9+:** Ensure you have a compatible Python version.
3.  **Node.js & CDK:** The AWS CDK is required to deploy the infrastructure. Follow the official AWS documentation to install it.

### Setup Steps

1.  **Deploy the CDK Stack:**
    -   Navigate to the `workflow-orchestrator/cdk/` directory.
    -   Install Node.js dependencies: `npm install`
    -   Deploy the stack using the provided script. This script will also generate the necessary `cdk-outputs.json`.
        ```bash
        ./cdk-with-env.sh deploy
        ```
    -   The output file will be created at `workflow-orchestrator/cdk/cdk-outputs.json`.

2.  **Install Python Test Dependencies:**
    -   From the project root, install the required Python packages for testing:
        ```bash
        pip install -r tests/requirements.txt
        ```
    -   The dependencies are:
        ```
        pytest
        pytest-bdd
        boto3
        python-dotenv
        pyyaml
        jsonschema
        requests
        msgpack
        botocore
        ```

## 5. Running the Tests

Once the environment is set up and the CDK stack is deployed, you can run the tests from the project root directory.

```bash
pytest tests/
```

You can also run a specific test file:

```bash
# Run the simplified workflow tests
pytest tests/test_merged_simplified_workflow.py

# Run the HITL error handling tests
pytest tests/test_hitl_error_handling_workflow.py
```

The tests are configured via `pytest.ini` and use fixtures defined in `tests/conftest.py` to manage resources like the AWS client and CDK outputs parser.

## 6. Next Steps & Future Work

The file `TASKS.md` outlines a comprehensive plan to implement a full BDD (Behavior-Driven Development) testing suite using Gherkin (`.feature` files). None of these tasks have been started. This represents the next major phase of work for the testing strategy. 