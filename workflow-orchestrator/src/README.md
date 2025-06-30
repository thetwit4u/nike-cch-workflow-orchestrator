## **CCH Data Contract Schemas**

This document provides the formal JSON Schema definitions for the key data contracts within the Customs Compliance Hub (CCH). These schemas serve as the source of truth for all message payloads.

### **1. Generic Command to Orchestrator Schema**

This schema defines the structure of the generic command message sent to the orchestrator-command-queue-dev SQS queue. It is used by the Trade Flow Controller to initiate a workflow and by Trade Compliance Capabilities to send back asynchronous responses.

**File:** [generic_command.schema.json](./generic_command.schema.json)



### **2. Capability Service Command Schema**

This schema defines the structure of the message sent *from* the Workflow Orchestrator *to* a Trade Compliance Capability SQS queue (e.g., capability-import-request-queue-dev). It aligns with the documented standard of using a header for metadata and a body for the functional payload.

**File:** [capability_command.schema.json](./capability_command.schema.json)

### **3. Workflow Definition JSON Schema
This document provides the formal JSON Schema that defines the structure of a *Workflow Definition* YAML file. This schema should be used for validation and to ensure consistency across all defined workflows.

**File:**  [workflow_definition.schema.json](./workflow_definition.schema.json)