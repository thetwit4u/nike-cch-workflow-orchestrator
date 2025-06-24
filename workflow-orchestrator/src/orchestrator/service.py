import logging
import os
from clients.queue_client import QueueClient
from lib.langgraph_checkpoint_dynamodb.langgraph_checkpoint_dynamodb.saver import DynamoDBSaver
from lib.langgraph_checkpoint_dynamodb.langgraph_checkpoint_dynamodb.config import DynamoDBConfig, DynamoDBTableConfig
from orchestrator.graph_executor import GraphExecutor
from utils.logging_adapter import WorkflowIdAdapter
from clients.s3_client import S3Client

logger = logging.getLogger(__name__)

class OrchestratorService:
    """
    The main service for orchestrating workflows.
    It handles command processing, state management, and interaction with the graph executor.
    """
    _instance = None

    def __init__(self):
        self.sqs_client = QueueClient()
        
        # Initialize the official DynamoDB checkpointer using the configuration
        table_name = os.environ.get("STATE_TABLE_NAME")
        if not table_name:
            raise ValueError("STATE_TABLE_NAME environment variable not set.")
        
        config = DynamoDBConfig(
            table_config=DynamoDBTableConfig(
                table_name=table_name,
            )
        )
        self.state_saver = DynamoDBSaver(config)

        # Pass the service instance itself to the executor
        self.graph_executor = GraphExecutor.get_instance(self)
        logger.info("OrchestratorService initialized.")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def process_command(self, command_message: dict):
        """
        Processes an incoming command message.
        """
        # Get the workflow ID for logging context
        instance_id = command_message.get('workflowInstanceId')
        
        # Create a logger adapter with the workflow ID
        adapter = WorkflowIdAdapter(logger, {'workflow_id': instance_id})
        
        command_type = command_message.get('command', {}).get('type')
        adapter.info(f"Processing command of type: {command_type}")

        if command_type == 'EVENT':
            self._handle_event_command(command_message, adapter)
        elif command_type == 'ASYNC_RESP':
            await self._handle_async_response(command_message, adapter)
        elif command_type == 'START_WORKFLOW':
            self._handle_start_workflow(command_message, adapter)
        elif command_type == 'RESUME_WORKFLOW':
            await self._handle_resume_workflow(command_message, adapter)
        else:
            adapter.warning(f"Unknown command type '{command_type}'. Ignoring.")

    def _handle_event_command(self, message: dict, adapter: logging.LoggerAdapter):
        """
        Handles the initial 'EVENT' command that starts a new workflow instance.
        """
        adapter.info("Handling EVENT command.")
        
        # 1. Extract details from the message
        workflow_uri = message.get('workflowDefinitionURI')
        instance_id = message.get('workflowInstanceId')
        initial_payload = message.get('command', {}).get('payload', {})
        correlation_id = message.get('correlationId')
        if correlation_id:
            initial_payload['correlationId'] = correlation_id

        if not all([workflow_uri, instance_id, initial_payload]):
            adapter.error("Missing required fields in EVENT command.")
            return

        # 2. Fetch workflow definition from S3
        s3_client = S3Client()
        definition = s3_client.get_workflow_definition(workflow_uri)

        # 3. (Future) Compile and execute the graph
        self.graph_executor.start_workflow(instance_id, definition, initial_payload, workflow_uri)
        
        adapter.info(f"Workflow instance initiated.")

    async def _handle_async_response(self, message: dict, adapter: logging.LoggerAdapter):
        """
        Handles an 'ASYNC_RESP' command from a capability.
        """
        adapter.info("Handling ASYNC_RESP command.")

        # 1. Extract details
        instance_id = message.get('workflowInstanceId')
        command = message.get('command', {})
        response_payload = command.get('payload', {})
        # The URI is now required on the response message for stateless resume
        workflow_definition_uri = message.get('workflowDefinitionURI')
        
        if not workflow_definition_uri:
            adapter.error(f"Cannot resume instance '{instance_id}': 'workflowDefinitionURI' missing from ASYNC_RESP message.")
            return

        # 2. Resume the graph with the new data
        self.graph_executor.resume_workflow(instance_id, workflow_definition_uri, response_payload)

        adapter.info(f"Resumed workflow instance with new data.")

    def _handle_start_workflow(self, message: dict, adapter: logging.LoggerAdapter):
        """
        Handles the 'START_WORKFLOW' command.
        """
        adapter.info("Handling START_WORKFLOW command.")
        
        command_type = message.get("command")
        payload = message.get("payload", {})
        
        if command_type == 'START_WORKFLOW':
            instance_id = payload.get("instance_id")
            workflow_uri = payload.get("workflow_uri")
            initial_payload = payload.get("initial_payload", {})

            if not all([instance_id, workflow_uri]):
                logger.error("Missing 'instance_id' or 'workflow_uri' for START_WORKFLOW")
                return
            
            # Fetch definition from S3 (assuming S3 client is part of the service now)
            s3_client = S3Client()
            definition = s3_client.get_workflow_definition(workflow_uri)

            self.graph_executor.start_workflow(instance_id, definition, initial_payload, workflow_uri)

    async def _handle_resume_workflow(self, message: dict, adapter: logging.LoggerAdapter):
        """
        Handles the 'RESUME_WORKFLOW' command.
        """
        adapter.info("Handling RESUME_WORKFLOW command.")

        # 1. Extract details
        instance_id = message.get('workflowInstanceId')
        command = message.get('command', {})
        response_payload = command.get('payload', {})
        # The URI is now required on the response message for stateless resume
        workflow_definition_uri = message.get('workflowDefinitionURI')
        
        if not workflow_definition_uri:
            adapter.error(f"Cannot resume instance '{instance_id}': 'workflowDefinitionURI' missing from RESUME_WORKFLOW message.")
            return

        # 2. Resume the graph with the new data
        self.graph_executor.resume_workflow(instance_id, workflow_definition_uri, response_payload)

        adapter.info(f"Resumed workflow instance with new data.")
