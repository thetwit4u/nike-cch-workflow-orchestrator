import logging
import os
from typing import Dict, Any
from clients.queue_client import QueueClient
from clients.http_client import HttpClient

from lib.langgraph_checkpoint_dynamodb.langgraph_checkpoint_dynamodb.saver import DynamoDBSaver
from lib.langgraph_checkpoint_dynamodb.langgraph_checkpoint_dynamodb.config import DynamoDBConfig, DynamoDBTableConfig
from orchestrator.graph_builder import GraphBuilder
from utils.logging_adapter import WorkflowIdAdapter
from clients.s3_client import S3Client
from langgraph.graph.graph import CompiledGraph
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
        self.state_saver = DynamoDBSaver(config=config)
        self.s3_client = S3Client()
        self.compiled_graphs: Dict[str, CompiledGraph] = {}
        self.graph_cache: Dict[str, CompiledGraph] = {}
        logger.info("OrchestratorService initialized.")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_or_compile_graph(self, workflow_definition_uri: str, command: dict) -> CompiledGraph:
        # Check for a test-only flag to bypass the cache
        if command.get("payload", {}).get("_no_cache"):
            logger.warning("'_no_cache' flag found. Bypassing graph cache.")
            graph = self._compile_graph(workflow_definition_uri, logger)
        else:
            graph = self.graph_cache.get(workflow_definition_uri)
            if not graph:
                logger.info(f"Compiling new graph for definition: {workflow_definition_uri}")
                graph = self._compile_graph(workflow_definition_uri, logger)
                self.graph_cache[workflow_definition_uri] = graph
            else:
                logger.info(f"Using cached graph for definition: {workflow_definition_uri}")
        
        return graph

    def _compile_graph(self, workflow_uri: str, adapter: logging.LoggerAdapter) -> CompiledGraph:
        """Compiles a graph from a URI and adds it to the cache."""
        s3 = S3Client()
        definition = s3.get_workflow_definition(workflow_uri)
        builder = GraphBuilder(definition, self.state_saver)
        graph = builder.compile_graph()
        self.graph_cache[workflow_uri] = graph
        return graph

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
            graph = self._get_or_compile_graph(workflow_uri, command)
            config = {"configurable": {"thread_id": instance_id}}
            
            # Distinguish between starting a new workflow and updating an existing one.
            current_state = graph.get_state(config)

            if command_type == 'EVENT':
                payload = command.get('payload', {})
                
                # Check if the state has any persisted values. If not, it's a new workflow.
                if not current_state.values:
                    # --- This is a NEW workflow ---
                    adapter.info("No existing state found. Starting new workflow.")
                    initial_context = {
                        "correlationId": command_message.get("correlationId"),
                        "workflowInstanceId": instance_id,
                        "workflow_definition_uri": workflow_uri,
                    }
                    # For a new workflow, the first invoke call creates the initial state and runs the graph.
                    initial_state_patch = {"context": initial_context, "data": payload}
                    adapter.info("Invoking graph with initial state to run to completion...")
                    final_state = graph.invoke(initial_state_patch, config)
                    adapter.info(f"Successfully ran workflow. Final state: {final_state}")
                
                else:
                    # --- This is an UPDATE to an existing workflow ---
                    # First, check if the workflow is past the point of no return.
                    if current_state.values.get("data", {}).get("filingpackCreationStarted"):
                        adapter.warning(f"Ignoring EVENT update because filing pack creation has already started.")
                        return # Do nothing

                    adapter.info("Existing state found. Updating and resuming workflow with new payload.")
                    # Add a flag to signal that this is a user-initiated update,
                    # which will cause the graph to loop back and recalculate.
                    payload['_user_update_request'] = True
                    # Merge the new payload into the existing state's data.
                    # The `WorkflowState` reducer for 'data' will handle the update.
                    graph.update_state(config, {"data": payload})
                    # Re-invoke the graph. If it was paused at a scheduled node, this will
                    # cause it to re-evaluate and create a new schedule with the updated data.
                    adapter.info("Re-invoking graph to run to completion...")
                    final_state = graph.invoke(None, config)
                    adapter.info(f"Successfully updated and resumed workflow. Final state: {final_state}")

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
                # This command is sent by the scheduler. Its purpose is to update the state
                # with a payload that was defined when the schedule was created.
                # This state update will, in turn, trigger the next step in the graph.
                state_update = payload.get('state_update', {})
                
                if state_update:
                    adapter.info(f"Applying deferred state update from scheduled task: {state_update}")
                    # Updating the state is what causes the graph to resume from its interrupted
                    # state and move to the next node.
                    graph.update_state(config, {"data": state_update, "context": state_update})
                else:
                    adapter.warning("Received an EXECUTE_SCHEDULED_TASK command with no state_update payload.")
                
                # After updating the state, we can simply invoke the graph with no input,
                # and it will continue from where it left off.
                final_state = graph.invoke(None, config)
                adapter.info(f"Successfully resumed workflow from scheduled task. Final state: {final_state}")

            else:
                adapter.warning(f"Unknown command type '{command_type}' cannot be processed.")

        except Exception as e:
            adapter.error(f"Error processing command for workflow '{instance_id}': {e}", exc_info=True)
