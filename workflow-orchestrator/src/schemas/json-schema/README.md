# JSON Schema Documentation

This directory contains JSON schema definitions that define and validate data structures used throughout the Nike Trade CCH project.

## Directory Structure

### `/asn`

Contains schemas related to Advanced Shipping Notices:

- `shipment-event-notification.schema.json` - Schema for asn event notifications
- `sample-data` - Example data for ASN schemas

### `/command`

Command schemas used for service communication:

- `capability/capability-command.schema.json` - Schema for capability-specific commands
- `orchestrator/generic-command.schema.json` - Schema for generic workflow orchestration commands

### `/deliveryset`

Schemas for delivery set data structures:

- `delivery-set.schema.json` - Core schema defining delivery set structure
- `sample-data/import-us.json` - Sample delivery set data for US imports

### `/sqs-event`

Schemas for SQS event messages:

- `s3-reference.schema.json` - Generic schema for S3 object references
- `delivery/delivery-set-s3-reference.schema.json` - Schema for including delivery set Id and its S3 object reference

### `/workflow`

Workflow definition schemas:

- `workflow-definition.schema.json` - Schema for defining workflow structures
- `sample-data/import_us_v1.yaml` - Sample workflow definition for US imports

## Usage

These schemas are used for:

1. Validation of data structures at runtime
2. Documentation of expected data formats
3. Code generation of corresponding model classes

## Schema Development Guidelines

When modifying schemas:

- Follow semantic versioning practices
- Update sample data to reflect schema changes