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
        
        # --- Start: New Command Parsing ---
        command_obj = command_message.get('command', {})
        command_type = command_obj.get('type')
        command_status = command_obj.get('status') # New required field for responses
        payload = command_obj.get('payload', {})
        workflow_uri = command_message.get('workflowDefinitionURI')

        adapter.info(f"Processing command of type: '{command_type}', status: '{command_status}'")

        if not CommandParser.is_valid_command(command_message):
            adapter.error("Invalid command structure against generic-command.schema.json.")
            return

        if not all([instance_id, command_type, workflow_uri]):
            adapter.error("Invalid command: Missing 'workflowInstanceId', 'command.type', or 'workflowDefinitionURI'.")
            return
        # --- End: New Command Parsing ---

        try:
            # For _get_or_compile_graph, we still need to check the payload of the initial event
            graph = self._get_or_compile_graph(workflow_uri, command_obj)
            config = {"configurable": {"thread_id": instance_id}}
            
            if command_type == 'EVENT':
                # Idempotency logic for EVENTs remains largely the same
                checkpoint = self.state_saver.get(config)
                if checkpoint is None:
                    adapter.info("No existing checkpoint found. Starting new workflow.")
                    initial_context = {
                        "correlationId": command_message.get("correlationId"),
                        "workflowInstanceId": instance_id,
                        "workflow_definition_uri": workflow_uri,
                    }
                    initial_state_patch = {"context": initial_context, "data": payload}
                    final_state = graph.invoke(initial_state_patch, config)
                    adapter.info(f"Successfully ran workflow. Final state: {final_state}")
                else:
                    # Duplicate event handling logic remains the same
                    adapter.info("Duplicate trigger event received for in-progress workflow. Ignoring.")
                    return

            elif command_type in ['ASYNC_RESP', 'HITL_RESP']:
                # Universal logic for resuming from a pause
                adapter.info(f"Processing {command_type} for main thread '{instance_id}'.")
                    
                current_state = graph.get_state(config)
                interrupted_node = self._get_interrupted_node(current_state)

                if not interrupted_node:
                    adapter.error(f"Could not determine interrupted node from state. Cannot proceed with {command_type}.")
                    return

                adapter.info(f"Workflow was interrupted at node: {interrupted_node}")

                workflow_definition = self.s3_client.get_workflow_definition(workflow_uri)
                node_def = workflow_definition.get("nodes", {}).get(interrupted_node)

                if command_type == 'ASYNC_RESP':
                    transition_node = node_def.get("on_response")
                else: # HITL_RESP
                    transition_node = node_def.get("on_success")

                if not transition_node:
                    adapter.error(f"Node '{interrupted_node}' is missing transition target for {command_type}.")
                    return
                    
                adapter.info(f"Transitioning to '{transition_node}'.")
                    
                # Merge the command's status and payload into the state's data channel
                # This makes the status available for condition nodes
                update_data = payload.copy()
                update_data['status'] = command_status
                
                graph.update_state(config, {"data": update_data}, as_node=transition_node)
                final_state = graph.invoke(None, config)
                adapter.info(f"Successfully resumed and ran workflow to completion. Final state: {final_state}")

            else:
                adapter.warning(f"Unknown command type '{command_type}' cannot be processed.")

        except Exception as e:
            adapter.error(f"Error processing command for workflow '{instance_id}': {e}", exc_info=True)

    def _get_interrupted_node(self, current_state: Any) -> str | None:
        """Helper to find the name of the node that was interrupted."""
        if current_state.next:
            # Standard interruption (e.g., async_request)
            interrupted_node_full_ns = current_state.next[0]
            return interrupted_node_full_ns.split(':')[0]
        else:
            # Fallback for other pause types (e.g., event_wait)
            for key in current_state.values:
                if key.startswith("branch:to:"):
                    return key.replace("branch:to:", "")
        return None
