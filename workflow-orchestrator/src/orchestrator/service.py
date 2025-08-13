import logging
import os
from typing import Dict, Any
from clients.queue_client import QueueClient
from clients.http_client import HttpClient

from lib.langgraph_checkpoint_dynamodb.langgraph_checkpoint_dynamodb.saver import DynamoDBSaver
from lib.langgraph_checkpoint_dynamodb.langgraph_checkpoint_dynamodb.config import DynamoDBConfig, DynamoDBTableConfig
from orchestrator.graph_builder import GraphBuilder
from orchestrator.event_publishing_checkpointer import EventPublishingCheckpointer
from orchestrator.event_publisher import EventPublisher
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
        self.event_publisher = EventPublisher()
        table_name = os.environ.get("STATE_TABLE_NAME")
        if not table_name:
            raise ValueError("STATE_TABLE_NAME environment variable not set.")
        
        config = DynamoDBConfig(table_config=DynamoDBTableConfig(table_name=table_name))
        self.state_saver = DynamoDBSaver(config=config)
        self.s3_client = S3Client()
        self.compiled_graphs: Dict[str, CompiledGraph] = {}
        self.graph_cache: Dict[str, CompiledGraph] = {}
        logger.info("OrchestratorService initialized. (v2)")

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

        # Create the checkpointer that will publish events.
        checkpointer_with_events = EventPublishingCheckpointer(
            saver=self.state_saver,
            event_publisher=self.event_publisher,
            workflow_definition=definition
        )

        builder = GraphBuilder(definition, checkpointer_with_events, self.event_publisher)
        graph = builder.compile_graph()
        self.graph_cache[workflow_uri] = (graph, definition) # Cache both
        return graph, definition


    @staticmethod
    def _deep_merge_dict(left: dict, right: dict) -> dict:
        """
        A reducer that deeply merges two dictionaries.
        Nested dictionaries are merged recursively.
        """
        if not isinstance(left, dict) or not isinstance(right, dict):
            return right
        
        merged = left.copy()
        for key, value in right.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = OrchestratorService._deep_merge_dict(merged[key], value)
            else:
                merged[key] = value
        return merged

    def process_command(self, command_message: dict):
        instance_id = command_message.get('workflowInstanceId')
        adapter = WorkflowIdAdapter(logger, {'workflow_id': instance_id})
        
        command_obj = command_message.get('command', {})
        command_type = command_obj.get('type')
        command_status = command_obj.get('status')
        payload = command_obj.get('payload', {})
        workflow_uri = command_message.get('workflowDefinitionURI')

        adapter.info(f"Processing command of type: '{command_type}', status: '{command_status}'")

        if not CommandParser.is_valid_command(command_message):
            adapter.error("Invalid command structure against generic-command.schema.json.")
            return

        if not all([instance_id, command_type, workflow_uri]):
            adapter.error("Invalid command: Missing 'workflowInstanceId', 'command.type', or 'workflowDefinitionURI'.")
            return

        try:
            graph, definition = self._get_or_compile_graph(workflow_uri, command_obj)
            config = {"configurable": {"thread_id": instance_id}}
            
            if command_type == 'EVENT':
                checkpoint = self.state_saver.get(config)
                if checkpoint is None:
                    adapter.info("No existing checkpoint found. Starting new workflow.")
                    initial_context = {
                        "correlationId": command_message.get("correlationId"),
                        "workflowInstanceId": instance_id,
                        "workflow_definition_uri": workflow_uri,
                    }
                    initial_state_patch = {"context": initial_context, "data": payload}

                    # --- Publish Start Event ---
                    entry_point_name = definition["entry_point"]
                    entry_node_def = definition["nodes"][entry_point_name]
                    next_step = {"name": entry_point_name, "title": entry_node_def.get("title", ""), "type": entry_node_def.get("type", "")}
                    self.event_publisher.publish_start_event(initial_state_patch, [next_step])
                    # ---

                    final_state = graph.invoke(initial_state_patch, config)
                    adapter.info(f"Successfully ran workflow. Final state: {final_state}")
                    adapter.info(f"Successfully ran workflow. Final state: {final_state}")

                    # Note: Do NOT drain-run at initial start. Doing so while paused at
                    # a non-fork async_request can duplicate outbound requests.
                else:
                    # Duplicate event handling logic remains the same
                    adapter.info("Duplicate trigger event received for in-progress workflow. Ignoring.")
                    return

            elif command_type in ['ASYNC_RESP', 'EVENT_WAIT_RESP']:
                routing_hint = command_obj.get("routingHint")
                branch_key = routing_hint.get("branchKey") if routing_hint else None

                if branch_key:
                    # This response targets a parallel branch; restore parent thread and context.
                    parent_config = {"configurable": {"thread_id": instance_id}}
                    parent_checkpoint = self.state_saver.get(parent_config)
                    if not parent_checkpoint:
                        adapter.error("Parent checkpoint not found; cannot resume branch.")
                        return

                    channel_values = parent_checkpoint.get("channel_values", {})
                    # If a dedicated branch thread exists, use it. Otherwise, stay on parent.
                    thread_id = None
                    for key, value in channel_values.items():
                        if key == f"branch_checkpoints.{branch_key}":
                            thread_id = value
                            break
                    if thread_id:
                        config = {"configurable": {"thread_id": thread_id}}
                    else:
                        # Fallback: no separate branch thread. Use parent and restore current_map_item from persisted map.
                        map_item = channel_values.get(f"map_items_by_key.{branch_key}")
                        if map_item is None:
                            adapter.error(f"Could not find thread_id or map item for branch key '{branch_key}'.")
                            return
                        config = {"configurable": {"thread_id": instance_id}}
                        graph.update_state(config, {"data": {"current_map_item": map_item}})
                else:
                    # This response is for the main thread.
                    config = {"configurable": {"thread_id": instance_id}}

                # Universal logic for resuming from a pause
                adapter.info(f"Processing {command_type} for thread '{config['configurable']['thread_id']}'.")
                
                current_state = graph.get_state(config)

                # Idempotency for ASYNC_RESP: ignore if not paused or already processed
                if command_type == 'ASYNC_RESP':
                    # If the graph is not currently paused on this thread, ignore late/duplicate responses
                    if not current_state.next:
                        adapter.info("Not paused on this thread; ignoring ASYNC_RESP as idempotent.")
                        return
                    dedupe_key = command_obj.get('in_reply_to') or command_obj.get('id')
                    processed = current_state.values.get('context', {}).get('processed_async_replies', {}) or {}
                    if processed.get(dedupe_key):
                        adapter.info(f"ASYNC_RESP with key '{dedupe_key}' already processed; ignoring.")
                        return
                    # Mark as processed immediately to protect from rapid duplicates
                    graph.update_state(config, {"context": {"processed_async_replies": {dedupe_key: True}}})

                interrupted_node = self._get_interrupted_node(current_state)

                if not interrupted_node:
                    adapter.error(f"Could not determine interrupted node from state. Cannot proceed with {command_type}.")
                    return

                adapter.info(f"Workflow was interrupted at node: {interrupted_node}")

                workflow_definition = self.s3_client.get_workflow_definition(workflow_uri)
                node_def = workflow_definition.get("nodes", {}).get(interrupted_node)

                if command_type == 'ASYNC_RESP':
                    transition_node = node_def.get("on_response")
                else: # EVENT_WAIT_RESP
                    transition_node = node_def.get("on_success")

                if not transition_node:
                    adapter.error(f"Node '{interrupted_node}' is missing transition target for {command_type}.")
                    return
                    
                adapter.info(f"Transitioning to '{transition_node}'.")
                    


                if command_type == 'ASYNC_RESP':
                    current_step_payload = {
                        "name": interrupted_node,
                        "title": node_def.get("title", ""),
                        "type": node_def.get("type", ""),
                    }
                    next_steps_payload = self._find_next_significant_nodes(transition_node, definition)
                    # Create a temporary state for the event publisher
                    event_state = current_state.values.copy()
                    event_state['data'] = self._deep_merge_dict(event_state.get('data', {}), payload)
                    event_state['data']['status'] = command_status

                    self.event_publisher.publish_event(
                        state=event_state,
                        current_step=current_step_payload,
                        next_steps=next_steps_payload,
                        status="Ended:AsyncRequest"
                    )
                # --- ---


                # Merge the command's status and payload into the state's data channel
                # This makes the status available for condition nodes
                update_data = payload.copy()
                update_data['status'] = command_status
                graph.update_state(config, {"data": update_data}, as_node=transition_node)
                final_state = graph.invoke(None, config)
                adapter.info(f"Successfully resumed and ran workflow to completion. Final state: {final_state}")

                # If we are transitioning into a map_fork, attempt a drain-run ONLY if not currently interrupted.
                transitioned_node_def = definition.get("nodes", {}).get(transition_node, {})
                if transitioned_node_def.get("type") == "map_fork":
                    current_state_after = graph.get_state(config)
                    if not current_state_after.next:
                        try:
                            self._drain_parallel_fanout(graph, config, adapter)
                        except Exception as drain_err:
                            adapter.warning(f"Drain-run after map_fork transition encountered an issue: {drain_err}")

               # --- Publish AsyncRequestEnded Event ---



            else:
                adapter.warning(f"Unknown command type '{command_type}' cannot be processed.")

        except Exception as e:
            adapter.error(f"Error processing command for workflow '{instance_id}': {e}", exc_info=True)

    def _drain_parallel_fanout(self, graph, config: dict, adapter: logging.LoggerAdapter):
        """
        Continue invoking the graph to allow all map_fork branches to start and
        dispatch their async requests. We loop until the number of registered
        branch checkpoints stabilizes for a couple of iterations or a max
        iteration threshold is reached.
        """
        max_iterations = 50
        stable_rounds_required = 2
        stable_rounds = 0
        previous_count = -1

        def count_registered_branches() -> int:
            checkpoint = self.state_saver.get(config)
            if not checkpoint:
                return 0
            channel_values = checkpoint.get("channel_values", {})
            return sum(1 for key in channel_values.keys() if key.startswith("branch_checkpoints."))

        for i in range(max_iterations):
            before = count_registered_branches()
            adapter.info(f"Drain-run iteration {i+1}: registered branches before step = {before}")

            # Advance execution; this lets LangGraph run pending Send tasks/branches
            graph.invoke(None, config)

            after = count_registered_branches()
            adapter.info(f"Drain-run iteration {i+1}: registered branches after step = {after}")

            if after == previous_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
            previous_count = after

            if stable_rounds >= stable_rounds_required:
                adapter.info("Drain-run complete: branch registration stabilized.")
                break

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

    def _find_next_significant_nodes(self, start_node_name: str, workflow_definition: Dict[str, Any]) -> list[Dict[str, Any]]:
        """
        Traverses the workflow definition from a starting node to find the next
        significant nodes (async_request or end).
        """
        significant_nodes = []
        nodes_to_visit = [start_node_name]
        visited_nodes = set()

        while nodes_to_visit:
            current_node_name = nodes_to_visit.pop(0)
            if current_node_name in visited_nodes:
                continue
            visited_nodes.add(current_node_name)

            node_def = workflow_definition.get("nodes", {}).get(current_node_name, {})
            node_type = node_def.get("type")

            if node_type in ["async_request", "end"]:
                significant_nodes.append({
                    "name": current_node_name,
                    "title": node_def.get("title", ""),
                    "type": node_type,
                })
            else:
                # Follow the success path for other nodes
                next_node = node_def.get("on_success") or node_def.get("on_response")
                if next_node:
                    nodes_to_visit.append(next_node)
                # Handle conditional branches
                elif node_type == "condition":
                    for branch_node in node_def.get("branches", {}).values():
                        nodes_to_visit.append(branch_node)

        return significant_nodes
