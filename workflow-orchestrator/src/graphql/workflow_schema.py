import graphene
from graphene import ObjectType, String, Int, DateTime, Boolean, List, Field, Mutation, Schema, ID
from enum import Enum
from typing import Optional, Dict, Any
import json
from datetime import datetime


class WorkflowStatus(graphene.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class NodeType(graphene.Enum):
    ASYNC_REQUEST = "async_request"
    LOG_ERROR = "log_error"
    END = "end"


class WorkflowNode(ObjectType):
    """Represents a single node in the workflow definition."""
    name = String(required=True)
    type = Field(NodeType, required=True)
    title = String()
    capability_id = String()
    input_keys = List(String)
    output_keys = List(String)
    on_response = String()
    on_failure = String()
    on_success = String()


class WorkflowDefinition(ObjectType):
    """Represents the workflow definition structure."""
    schema_version = String(required=True)
    workflow_name = String(required=True)
    workflow_id = String(required=True)
    entry_point = String(required=True)
    initial_context = List(String)
    nodes = List(WorkflowNode)
    
    def resolve_nodes(self, info):
        """Convert the nodes dictionary to a list of WorkflowNode objects."""
        # This would typically be loaded from the workflow YAML file
        return []


class WorkflowExecutionContext(ObjectType):
    """Represents the current execution context of a workflow instance."""
    current_node = String()
    delivery_set_id = String()
    delivery_set_uri = String()
    delivery_set_import_enriched_id = String()
    delivery_set_import_enriched_uri = String()
    delivery_set_import_enriched_status = String()
    delivery_set_import_enriched_error = String()
    filing_packs_status = String()
    filing_packs_error = String()
    import_filing_packs = String()
    internal_error = String()


class WorkflowInstance(ObjectType):
    """Represents a running or completed workflow instance."""
    workflow_instance_id = ID(required=True)
    correlation_id = String()
    workflow_definition_uri = String()
    workflow_definition = Field(WorkflowDefinition)
    status = Field(WorkflowStatus, required=True)
    current_node = String()
    created_at = DateTime()
    updated_at = DateTime()
    completed_at = DateTime()
    execution_context = Field(WorkflowExecutionContext)
    error_message = String()
    execution_time_seconds = Int()
    
    def resolve_execution_time_seconds(self, info):
        """Calculate execution time if workflow is completed."""
        if self.completed_at and self.created_at:
            return int((self.completed_at - self.created_at).total_seconds())
        return None


class WorkflowExecutionMetrics(ObjectType):
    """Aggregate metrics for workflow executions."""
    total_executions = Int()
    successful_executions = Int()
    failed_executions = Int()
    average_execution_time_seconds = Int()
    executions_by_status = String()  # JSON string of status counts
    
    def resolve_executions_by_status(self, info):
        """Return execution counts by status as JSON."""
        # This would typically query the database
        return json.dumps({
            "COMPLETED": 150,
            "FAILED": 10,
            "RUNNING": 5,
            "PENDING": 2
        })


class DeliverySetInput(graphene.InputObjectType):
    """Input type for starting a workflow with delivery set data."""
    delivery_set_id = String(required=True)
    delivery_set_uri = String(required=True)
    no_cache = Boolean(default_value=False)


class WorkflowStartInput(graphene.InputObjectType):
    """Input type for starting a workflow."""
    workflow_definition_uri = String(required=True)
    delivery_set = Field(DeliverySetInput, required=True)
    correlation_id = String()


class StartWorkflow(Mutation):
    """Mutation to start a new workflow instance."""
    
    class Arguments:
        input = WorkflowStartInput(required=True)
    
    workflow_instance = Field(WorkflowInstance)
    success = Boolean()
    message = String()
    
    def mutate(self, info, input):
        """Start a new workflow instance."""
        try:
            # Generate workflow instance ID
            import uuid
            workflow_instance_id = str(uuid.uuid4())
            
            # Create workflow instance (this would typically interact with your workflow orchestrator)
            workflow_instance = WorkflowInstance(
                workflow_instance_id=workflow_instance_id,
                correlation_id=input.correlation_id or workflow_instance_id,
                workflow_definition_uri=input.workflow_definition_uri,
                status=WorkflowStatus.PENDING,
                created_at=datetime.now(),
                execution_context=WorkflowExecutionContext(
                    delivery_set_id=input.delivery_set.delivery_set_id,
                    delivery_set_uri=input.delivery_set.delivery_set_uri
                )
            )
            
            # Here you would typically:
            # 1. Send message to SQS queue
            # 2. Save workflow instance to database
            # 3. Return the created instance
            
            return StartWorkflow(
                workflow_instance=workflow_instance,
                success=True,
                message=f"Workflow {workflow_instance_id} started successfully"
            )
            
        except Exception as e:
            return StartWorkflow(
                workflow_instance=None,
                success=False,
                message=f"Failed to start workflow: {str(e)}"
            )


class CancelWorkflow(Mutation):
    """Mutation to cancel a running workflow."""
    
    class Arguments:
        workflow_instance_id = ID(required=True)
    
    success = Boolean()
    message = String()
    
    def mutate(self, info, workflow_instance_id):
        """Cancel a running workflow."""
        try:
            # Here you would typically:
            # 1. Update workflow status to CANCELLED
            # 2. Send cancellation message to workflow orchestrator
            # 3. Clean up any resources
            
            return CancelWorkflow(
                success=True,
                message=f"Workflow {workflow_instance_id} cancelled successfully"
            )
            
        except Exception as e:
            return CancelWorkflow(
                success=False,
                message=f"Failed to cancel workflow: {str(e)}"
            )


class Query(ObjectType):
    """Root query type for workflow GraphQL API."""
    
    # Single workflow instance queries
    workflow_instance = Field(
        WorkflowInstance, 
        workflow_instance_id=ID(required=True),
        description="Get a specific workflow instance by ID"
    )
    
    # List workflow instances with filtering
    workflow_instances = List(
        WorkflowInstance,
        status=Field(WorkflowStatus),
        limit=Int(default_value=50),
        offset=Int(default_value=0),
        description="List workflow instances with optional filtering"
    )
    
    # Workflow definition queries
    workflow_definition = Field(
        WorkflowDefinition,
        workflow_id=String(required=True),
        description="Get workflow definition by ID"
    )
    
    workflow_definitions = List(
        WorkflowDefinition,
        description="List all available workflow definitions"
    )
    
    # Metrics and analytics
    workflow_metrics = Field(
        WorkflowExecutionMetrics,
        workflow_id=String(),
        date_from=DateTime(),
        date_to=DateTime(),
        description="Get workflow execution metrics"
    )
    
    # Health check
    workflow_orchestrator_health = Boolean(description="Check if workflow orchestrator is healthy")
    
    def resolve_workflow_instance(self, info, workflow_instance_id):
        """Resolve a single workflow instance."""
        # This would typically query your database
        return WorkflowInstance(
            workflow_instance_id=workflow_instance_id,
            status=WorkflowStatus.RUNNING,
            current_node="Create_Filing_Packs",
            created_at=datetime.now(),
            execution_context=WorkflowExecutionContext(
                current_node="Create_Filing_Packs",
                delivery_set_id="test-delivery-set-123",
                delivery_set_uri="s3://test-bucket/delivery-set.json"
            )
        )
    
    def resolve_workflow_instances(self, info, status=None, limit=50, offset=0):
        """Resolve list of workflow instances."""
        # This would typically query your database with filtering
        instances = []
        for i in range(limit):
            instances.append(WorkflowInstance(
                workflow_instance_id=f"workflow-{i + offset}",
                status=status or WorkflowStatus.COMPLETED,
                created_at=datetime.now(),
                current_node="End_Workflow" if status == WorkflowStatus.COMPLETED else "Create_Filing_Packs"
            ))
        return instances
    
    def resolve_workflow_definition(self, info, workflow_id):
        """Resolve workflow definition."""
        return WorkflowDefinition(
            schema_version="1.1.2",
            workflow_name="Simplified Import US Workflow",
            workflow_id=workflow_id,
            entry_point="Create_Filing_Packs",
            initial_context=["deliverySetId", "deliverySetURI"]
        )
    
    def resolve_workflow_definitions(self, info):
        """Resolve all workflow definitions."""
        return [
            WorkflowDefinition(
                schema_version="1.1.2",
                workflow_name="Simplified Import US Workflow",
                workflow_id="import_us_v1_simplified",
                entry_point="Create_Filing_Packs",
                initial_context=["deliverySetId", "deliverySetURI"]
            )
        ]
    
    def resolve_workflow_metrics(self, info, workflow_id=None, date_from=None, date_to=None):
        """Resolve workflow execution metrics."""
        return WorkflowExecutionMetrics(
            total_executions=167,
            successful_executions=150,
            failed_executions=10,
            average_execution_time_seconds=45
        )
    
    def resolve_workflow_orchestrator_health(self, info):
        """Check workflow orchestrator health."""
        # This would typically ping your orchestrator service
        return True


class Mutation(ObjectType):
    """Root mutation type for workflow GraphQL API."""
    start_workflow = StartWorkflow.Field()
    cancel_workflow = CancelWorkflow.Field()


# Create the GraphQL schema
workflow_schema = Schema(query=Query, mutation=Mutation)


# GraphQL subscription support (optional)
class Subscription(ObjectType):
    """Root subscription type for real-time workflow updates."""
    
    workflow_status_updates = Field(
        WorkflowInstance,
        workflow_instance_id=ID(required=True),
        description="Subscribe to workflow status updates"
    )
    
    def resolve_workflow_status_updates(self, info, workflow_instance_id):
        """Subscribe to workflow status updates."""
        # This would typically use a pub/sub mechanism
        # For now, return a placeholder
        return WorkflowInstance(
            workflow_instance_id=workflow_instance_id,
            status=WorkflowStatus.RUNNING,
            current_node="Create_Filing_Packs",
            created_at=datetime.now()
        )


# Extended schema with subscriptions
workflow_schema_with_subscriptions = Schema(
    query=Query, 
    mutation=Mutation, 
    subscription=Subscription
)