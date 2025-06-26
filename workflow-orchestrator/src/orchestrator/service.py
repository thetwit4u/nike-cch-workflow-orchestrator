import logging
import os
from typing import Dict, Any
from clients.queue_client import QueueClient
from clients.http_client import HttpClient
from lib.langgraph_checkpoint_dynamodb.saver import DynamoDBSaver
from lib.langgraph_checkpoint_dynamodb.config import DynamoDBConfig, DynamoDBTableConfig
from orchestrator.graph_builder import GraphBuilder
from utils.logging_adapter import WorkflowIdAdapter
from clients.s3_client import S3Client
from langgraph.graph import CompiledGraph
from utils.command_parser import CommandParser

logger = logging.getLogger(__name__)

class OrchestratorService:
    """
    The main service for orchestrating workflows.
    It handles command processing, state management, and graph compilation.
    """
    _instance = None

    def __init__(self):
        self.sqs_client = QueueClient()
        self.http_client = HttpClient()
        table_name = os.environ.get("STATE_TABLE_NAME")
        if not table_name:
            raise ValueError("STATE_TABLE_NAME environment variable not set.")
        
        config = DynamoDBConfig(table_config=DynamoDBTableConfig(table_name=table_name))
        self.state_saver = DynamoDBSaver(config)
        self.s3_client = S3Client()
        self.compiled_graphs: Dict[str, CompiledGraph] = {}
        logger.info("OrchestratorService initialized.")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_or_compile_graph(self, workflow_definition_uri: str) -> CompiledGraph:
        if workflow_definition_uri not in self.compiled_graphs:
            logger.info(f"Compiling new graph for definition: {workflow_definition_uri}")
            definition = self.s3_client.get_workflow_definition(workflow_definition_uri)
            builder = GraphBuilder(definition, self.state_saver)
            graph = builder.compile_graph()
            self.compiled_graphs[workflow_definition_uri] = graph
        return self.compiled_graphs[workflow_definition_uri]

    def process_command(self, command_message: dict):
        instance_id = command_message.get('workflowInstanceId')
        adapter = WorkflowIdAdapter(logger, {'workflow_id': instance_id})
        
        command = command_message.get('command', {})
        command_type = command.get('type')
        workflow_uri = command_message.get('workflowDefinitionURI')

        adapter.info(f"Processing command of type: {command_type}")

        # Bypass validation for internal commands
        if command_type != 'EXECUTE_SCHEDULED_TASK':
            if not CommandParser.is_valid_command(command_message):
                adapter.error("Invalid command structure against generic_command.schema.json.")
                return

        if not all([instance_id, command_type, workflow_uri]):
            adapter.error("Invalid command: Missing 'workflowInstanceId', 'command.type', or 'workflowDefinitionURI'.")
            return

        try:
            graph = self._get_or_compile_graph(workflow_uri)
            config = {"configurable": {"thread_id": instance_id}}
            payload = command.get('payload', {})

            if command_type == 'EVENT':
                initial_context = {
                    **payload,
                    "correlationId": command_message.get("correlationId"),
                    "workflowInstanceId": instance_id,
                    "workflow_definition_uri": workflow_uri,
                }
                graph.invoke({"context": initial_context, "data": payload}, config)
                adapter.info("Successfully started workflow.")

            elif command_type == 'ASYNC_RESP':
                routing_hint = command.get('routingHint')
                thread_id = instance_id

                if routing_hint:
                    branch_key = routing_hint.get('branchKey')
                    parent_state = graph.get_state(config)
                    checkpoints = parent_state.values.get('branch_checkpoints', {})
                    child_thread_id = checkpoints.get(branch_key)

                    if child_thread_id:
                        thread_id = child_thread_id
                        adapter.info(f"Routing ASYNC_RESP to child thread '{child_thread_id}'.")
                    else:
                        adapter.error(f"Could not find registered child thread for branch key '{branch_key}'.")
            return

                resume_config = {"configurable": {"thread_id": thread_id}}
                graph.update_state(resume_config, {"context": payload, "data": payload})
                graph.invoke(None, resume_config)
                adapter.info(f"Successfully resumed workflow on thread '{thread_id}'.")

            elif command_type == 'EXECUTE_SCHEDULED_TASK':
                state_update = payload.get('state_update', {})
                next_command = payload.get('next_command', {})

                if state_update:
                    graph.update_state(config, {"data": state_update})
                    adapter.info(f"Updated state with: {state_update} before sending scheduled command.")

                original_context = payload.get('original_context', {})
                original_data = payload.get('original_data', {})
                temp_state = {"context": original_context, "data": {**original_data, **state_update}}
                
                command_parser = CommandParser(temp_state, next_command)
                capability_command = command_parser.create_command_message("ASYNC_REQ")

                capability_id = next_command.get("capability_id")
                if not capability_id or '#' not in capability_id:
                    raise ValueError(f"Invalid capability_id format: '{capability_id}'. Expected 'service#action'.")

                capability_service = capability_id.split('#')[0].upper()
                
                # Check for a test-specific HTTP endpoint first.
                test_endpoint_var = f"CCH_MOCK_HTTP_ENDPOINT_{capability_service}"
                http_endpoint = os.environ.get(test_endpoint_var)

                if http_endpoint:
                    logger.info(f"Sending ASYNC_REQ to HTTP endpoint for capability '{capability_id}' from scheduled task.")
                    self.http_client.post(http_endpoint, capability_command)
                else:
                    # Fallback to the production SQS queue configuration.
                    prod_queue_var = f"CCH_CAPABILITY_{capability_service}"
                    sqs_queue = os.environ.get(prod_queue_var)
                    if not sqs_queue:
                        raise ValueError(f"Endpoint for capability service '{capability_service}' is not configured. Checked for '{test_endpoint_var}' and '{prod_queue_var}'.")

                    logger.info(f"Sending ASYNC_REQ to SQS queue for capability '{capability_id}' from scheduled task.")
                    self.sqs_client.send_message(sqs_queue, capability_command)

                adapter.info(f"Successfully sent ASYNC_REQ for capability '{capability_id}' from scheduled task.")
            
            else:
                adapter.warning(f"Unknown command type '{command_type}' cannot be processed.")

        except Exception as e:
            adapter.error(f"Error processing command for workflow '{instance_id}': {e}", exc_info=True)
