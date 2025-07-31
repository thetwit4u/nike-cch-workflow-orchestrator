# CCH System Event Publishing: Implementation Plan

**Owner:** David Vanheeswijck  
**Status:** For Implementation

This document outlines the technical plan to implement a standardized system for event publishing across CCH components, as detailed in `STORY.md` and `Technical Requirements_ Generic System Event Publishing.md`.

## 1. Overview

The goal is to establish a central, decoupled, and strictly ordered event bus using AWS SNS FIFO. This will allow any CCH component to publish significant business or system events.

To support future migration to a centrally managed infrastructure, the provisioning will be conditional:
- If an `SNS_TOPIC_CCH_EVENTS_ARN` environment variable is provided, the stack will use that existing topic.
- If the variable is not provided, the stack will create a *fallback* SNS FIFO topic for use in isolated or development environments.

The implementation involves:
*   **Conditional Infrastructure Provisioning:** Using CDK to either create a fallback SNS FIFO topic or reference an existing one.
*   **Granting Permissions:** Ensuring the Workflow Orchestrator has `sns:Publish` permissions for the resolved topic.
*   **Application Logic:** Implementing a reusable publisher utility within the orchestrator to send events.
*   **Schema Management:** Adding the formal `CHHSystemEvent` JSON schema to the project.

## 2. Task Breakdown

### Task 2.1: CDK Infrastructure Provisioning

All infrastructure changes will be made within the `CchWorkflowOrchestratorStack`. The logic will be conditional based on the presence of the `SNS_TOPIC_CCH_EVENTS_ARN` environment variable.

**File to Modify:** `/Users/dvan97/dev/nike/repos/cch/cch-flow-orchestrator/workflow-orchestrator/cdk/lib/cdk-stack.ts`

**Steps:**

1.  **Read Environment Variable:**
    *   In the CDK stack constructor, read the `SNS_TOPIC_CCH_EVENTS_ARN` from `process.env`.

2.  **Conditionally Define/Import the SNS Topic:**
    *   Declare a variable for the topic, e.g., `let systemEventsTopic: sns.ITopic;`.
    *   **If `SNS_TOPIC_CCH_EVENTS_ARN` is provided:**
        *   Import the existing topic using its ARN.
        *   `systemEventsTopic = sns.Topic.fromTopicArn(this, 'ImportedSystemEventsTopic', process.env.SNS_TOPIC_CCH_EVENTS_ARN);`
    *   **If `SNS_TOPIC_CCH_EVENTS_ARN` is NOT provided (Fallback creation):**
        *   Create a new `sns.Topic` resource as a fallback.
        *   The `topicName` must be constructed to include the environment, owner suffix, and the `.fifo` suffix (e.g., `cch-system-events-fallback-${env}${ownerSuffix}.fifo`).
        *   Set `fifo: true`.
        *   Enable `contentBasedDeduplication: true`.
        *   Assign this new topic to the `systemEventsTopic` variable.
        *   Create a `CfnOutput` for the `FallbackSystemEventsTopicArn` (`systemEventsTopic.topicArn`). This makes the ARN of the *created* topic discoverable.

3.  **Grant Publish Permissions to Orchestrator:**
    *   Locate the IAM Role associated with the `orchestratorLambda`.
    *   Use the CDK's grant mechanism to add `sns:Publish` permissions for the resolved `systemEventsTopic` to the Lambda's role.
    *   Example: `systemEventsTopic.grantPublish(this.orchestratorLambda.role);`

4.  **Configure Lambda Environment Variable:**
    *   Add a new environment variable to the `orchestratorLambda` definition.
    *   **Variable Name:** `SYSTEM_EVENTS_TOPIC_ARN`
    *   **Value:** The ARN of the resolved topic (`systemEventsTopic.topicArn`). This will be the provided ARN or the ARN of the newly created fallback topic.

5.  **Add CloudFormation Output for Orchestrator:**
    *   Create a `CfnOutput` for the `SystemEventsTopicArn` with the value of `systemEventsTopic.topicArn`. This makes the ARN being used by the orchestrator easily discoverable, regardless of whether it was imported or created.

### Task 2.2: Add CHHSystemEvent Schema

To maintain consistency with existing data contracts, the new event schema will be added to the project.

1.  **Create New File:**
    *   `/Users/dvan97/dev/nike/repos/cch/cch-flow-orchestrator/workflow-orchestrator/src/schemas/cch_system_event.schema.json`

2.  **Populate Schema:**
    *   Copy the complete JSON schema definition from the `Technical Requirements_ Generic System Event Publishing.md` document into this new file.

3.  **Update Documentation (Optional but Recommended):**
    *   Add a reference to the new `cch_system_event.schema.json` in `/Users/dvan97/dev/nike/repos/cch/cch-flow-orchestrator/workflow-orchestrator/src/README.md`.

### Task 2.3: Event Publishing in Orchestrator

**Files to Modify:** `workflow-orchestrator/src/...`

**Acceptance Criteria:**

*   After a node is executed, the orchestrator constructs a valid `CHHSystemEvent` object.
*   The `source` field is correctly set to "WorkflowOrchestrator".
*   The optional `workflowContext` and `transition` objects are present and fully populated.
*   If the triggering command contained messages, the optional `messages` array is present and correctly populated.
*   The completed event object is successfully published to the `cch-system-events.fifo` SNS topic.
*   The `sns:Publish` call MUST include a `MessageGroupId`. This ID MUST be the `workflowInstanceId` to ensure all events for a single workflow are processed in order.
*   The publishing action includes robust error handling and logging, ensuring that a failure to publish does not fail the main workflow.
