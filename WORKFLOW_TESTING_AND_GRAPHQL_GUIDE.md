# Workflow Testing and GraphQL API Guide

## Overview

This guide covers testing the merged simplified workflow and using the GraphQL API for workflow visualization and management.

## Merged Workflow Changes

The simplified workflow has been consolidated from **2 async steps** to **1 async step**:

### Before (2 Steps)
1. `Enrich_Delivery_Set` - Enriches delivery set data
2. `Create_Filing_Packs` - Creates filing packs from enriched data

### After (1 Step)
1. `Create_Filing_Packs` - Performs both enrichment and filing pack creation

### Key Changes
- **Target capability**: `import#create_filingpacks`
- **Input**: Delivery set model fields (`deliverySetId`, `deliverySetURI`)
- **Output**: Combined enriched delivery model + filing pack info
- **Performance**: Reduced network overhead and improved execution time

## Test Script Usage

### Prerequisites
```bash
# Install test dependencies
pip install -r tests/requirements.txt

# Ensure CDK stack is deployed
cd workflow-orchestrator/cdk
cdk deploy
```

### Running Tests

#### Run All Merged Workflow Tests
```bash
# Run all tests for the merged workflow
pytest tests/test_merged_simplified_workflow.py -v

# Run specific test
pytest tests/test_merged_simplified_workflow.py::test_merged_workflow_happy_path -v
```

#### Test Categories

1. **Happy Path Test** (`test_merged_workflow_happy_path`)
   - Tests successful execution with valid delivery set data
   - Verifies both enrichment and filing pack creation outputs
   - Validates proper workflow completion

2. **Error Path Test** (`test_merged_workflow_error_path`)
   - Tests error handling with invalid data
   - Verifies error information is captured
   - Ensures workflow completes via error handler

3. **Missing S3 File Test** (`test_merged_workflow_missing_s3_file`)
   - Tests handling of missing S3 files
   - Verifies robust error handling

4. **Performance Test** (`test_merged_workflow_performance`)
   - Measures execution time of merged workflow
   - Validates completion within reasonable timeframe
   - Compares performance against original two-step approach

### Test Data Structure

The tests use comprehensive delivery set data including:
- Consignment information (BOL, discharge dates, transport details)
- Delivery information (shipment numbers, importer details)
- Delivery items (products, valuations, commodity breakdowns)
- Address and partner information

## GraphQL API for Workflow Management

### Setup

#### Install Dependencies
```bash
# Install GraphQL API dependencies
pip install -r workflow-orchestrator/src/graphql/requirements.txt
```

#### Start the API Server
```bash
# Start development server
cd workflow-orchestrator/src/graphql
python api_server.py

# Or with uvicorn directly
uvicorn api_server:app --reload --port 8000
```

### API Endpoints

#### GraphQL Endpoints
- `GET /graphql` - GraphQL Playground
- `POST /graphql` - GraphQL queries and mutations
- `POST /graphql/execute` - Enhanced GraphQL execution with error handling
- `GET /graphql/schema` - Schema introspection

#### REST Endpoints
- `GET /health` - Health check
- `GET /workflows` - List workflows with filtering
- `GET /workflows/{id}` - Get specific workflow
- `POST /workflows/start` - Start new workflow
- `POST /workflows/{id}/cancel` - Cancel workflow
- `GET /metrics` - Workflow metrics
- `WS /ws/workflows/{id}` - Real-time workflow updates

### GraphQL Schema

#### Key Types

```graphql
type WorkflowInstance {
  workflowInstanceId: ID!
  correlationId: String
  status: WorkflowStatus!
  currentNode: String
  createdAt: DateTime
  completedAt: DateTime
  executionContext: WorkflowExecutionContext
  executionTimeSeconds: Int
}

type WorkflowExecutionContext {
  deliverySetId: String
  deliverySetURI: String
  deliverySetImportEnrichedId: String
  deliverySetImportEnrichedStatus: String
  filingPacksStatus: String
  importFilingPacks: String
}

enum WorkflowStatus {
  PENDING
  RUNNING
  COMPLETED
  FAILED
  CANCELLED
}
```

#### Example Queries

##### Get Workflow Instance
```graphql
query GetWorkflow($id: ID!) {
  workflowInstance(workflowInstanceId: $id) {
    workflowInstanceId
    status
    currentNode
    createdAt
    executionContext {
      deliverySetId
      deliverySetImportEnrichedStatus
      filingPacksStatus
    }
  }
}
```

##### List Workflows
```graphql
query ListWorkflows($status: WorkflowStatus, $limit: Int) {
  workflowInstances(status: $status, limit: $limit) {
    workflowInstanceId
    status
    currentNode
    createdAt
    executionTimeSeconds
  }
}
```

##### Get Metrics
```graphql
query GetMetrics($workflowId: String) {
  workflowMetrics(workflowId: $workflowId) {
    totalExecutions
    successfulExecutions
    failedExecutions
    averageExecutionTimeSeconds
    executionsByStatus
  }
}
```

#### Example Mutations

##### Start Workflow
```graphql
mutation StartWorkflow($input: WorkflowStartInput!) {
  startWorkflow(input: $input) {
    workflowInstance {
      workflowInstanceId
      status
      createdAt
    }
    success
    message
  }
}
```

Variables:
```json
{
  "input": {
    "workflowDefinitionUri": "s3://definitions/import_us_v1.1.1-simplified.yaml",
    "deliverySet": {
      "deliverySetId": "test-delivery-123",
      "deliverySetUri": "s3://data/delivery-set.json"
    }
  }
}
```

##### Cancel Workflow
```graphql
mutation CancelWorkflow($workflowId: ID!) {
  cancelWorkflow(workflowInstanceId: $workflowId) {
    success
    message
  }
}
```

### REST API Examples

#### Start Workflow
```bash
curl -X POST "http://localhost:8000/workflows/start" \
  -H "Content-Type: application/json" \
  -d '{
    "workflowDefinitionUri": "s3://definitions/import_us_v1.1.1-simplified.yaml",
    "deliverySet": {
      "deliverySetId": "test-delivery-123",
      "deliverySetUri": "s3://data/delivery-set.json"
    }
  }'
```

#### List Workflows
```bash
curl "http://localhost:8000/workflows?status=RUNNING&limit=10"
```

#### Get Workflow Details
```bash
curl "http://localhost:8000/workflows/workflow-123"
```

#### Get Metrics
```bash
curl "http://localhost:8000/metrics?workflow_id=import_us_v1_simplified"
```

### Real-time Updates

#### WebSocket Connection
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/workflows/workflow-123');

ws.onmessage = function(event) {
  const update = JSON.parse(event.data);
  console.log('Workflow update:', update);
};
```

#### GraphQL Subscriptions
```graphql
subscription WorkflowUpdates($workflowId: ID!) {
  workflowStatusUpdates(workflowInstanceId: $workflowId) {
    workflowInstanceId
    status
    currentNode
    timestamp
  }
}
```

## Performance Benefits

### Merged Workflow Advantages
1. **Reduced Latency**: Single async call vs. two sequential calls
2. **Lower Network Overhead**: Fewer round trips
3. **Simplified Error Handling**: Single point of failure
4. **Better Resource Utilization**: Combined processing
5. **Improved Throughput**: Faster overall execution

### Expected Performance Improvements
- **Execution Time**: ~30-40% reduction
- **Network Calls**: 50% reduction (2 calls → 1 call)
- **Error Recovery**: Simplified error handling paths

## Monitoring and Observability

### Key Metrics to Monitor
- Workflow execution times
- Success/failure rates
- Error distributions
- Resource utilization
- Queue depths

### Alerting
- Failed workflow executions
- Execution time thresholds
- Error rate spikes
- Resource exhaustion

## Best Practices

### Testing
1. Always test both happy path and error scenarios
2. Use realistic test data that matches production
3. Test performance under load
4. Validate error handling and recovery

### GraphQL API
1. Use pagination for large result sets
2. Implement proper error handling
3. Monitor query complexity
4. Use subscriptions for real-time updates
5. Implement authentication/authorization for production

### Workflow Management
1. Use correlation IDs for tracking
2. Implement proper logging
3. Monitor execution patterns
4. Set up alerts for failures
5. Regular performance reviews

## Troubleshooting

### Common Issues
1. **Test Failures**: Check AWS credentials and CDK deployment
2. **GraphQL Errors**: Verify schema and dependencies
3. **Performance Issues**: Monitor resource usage and network latency
4. **Timeout Errors**: Adjust timeout settings and monitor execution times

### Debug Commands
```bash
# Run tests with verbose output
pytest tests/test_merged_simplified_workflow.py -v -s

# Check API health
curl http://localhost:8000/health

# View GraphQL schema
curl http://localhost:8000/graphql/schema
```

## Conclusion

The merged workflow provides significant performance improvements while maintaining functionality. The GraphQL API offers comprehensive workflow management and visualization capabilities, making it easier to monitor and manage workflow executions in production environments.