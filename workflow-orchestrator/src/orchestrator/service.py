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
from orchestrator.next_steps import compute_next_steps
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

                    # Note: Do NOT drain-run at initial start. Doing so while paused at
                    # a non-fork async_request can duplicate outbound requests.
                else:
                    # Duplicate event handling logic remains the same
                    adapter.info("Duplicate trigger event received for in-progress workflow. Ignoring.")
                    return

            elif command_type in ['ASYNC_RESP', 'EVENT_WAIT_RESP']:
                routing_hint = command_obj.get("routingHint")
                branch_key = routing_hint.get("branchKey") if routing_hint else None
                is_branch_response = bool(branch_key)

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
                            # Try nested context map persisted during drain-run
                            parent_state = graph.get_state(parent_config)
                            nested_map = parent_state.values.get('context', {}).get('map_items_by_key', {}) or {}
                            map_item = nested_map.get(branch_key)
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

                # Idempotency for ASYNC_RESP: for branch responses, we proceed even if parent thread isn't paused
                if command_type == 'ASYNC_RESP':
                    if not is_branch_response:
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

                # Determine interrupted node.
                interrupted_node = None
                if is_branch_response:
                    if command_type == 'EVENT_WAIT_RESP':
                        # Prefer the currently paused event_wait node if present
                        current_node_name = current_state.values.get('context', {}).get('current_node')
                        if current_node_name:
                            maybe_node_def = definition.get("nodes", {}).get(current_node_name, {})
                            if maybe_node_def.get("type") == "event_wait":
                                interrupted_node = current_node_name

                        # If not found, find an event_wait node for branch updates (one using 'on_event')
                        if not interrupted_node:
                            for node_name, node_def in definition.get("nodes", {}).items():
                                if node_def.get("type") == "event_wait" and node_def.get("on_event"):
                                    interrupted_node = node_name
                                    break
                    else:
                        # ASYNC_RESP for a branch: find the async node that consumes current_map_item
                        for node_name, node_def in definition.get("nodes", {}).items():
                            if node_def.get("type") == "async_request" and 'current_map_item' in (node_def.get('input_keys') or []):
                                interrupted_node = node_name
                                break

                if not interrupted_node:
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
                    # Support both 'on_event' and 'on_success' for event_wait nodes
                    if node_def.get("type") == "event_wait":
                        transition_node = node_def.get("on_event") or node_def.get("on_success")
                    else:
                        transition_node = node_def.get("on_success")

                if not transition_node:
                    adapter.error(f"Node '{interrupted_node}' is missing transition target for {command_type}.")
                    return
                    
                adapter.info(f"Transitioning to '{transition_node}'.")
                    


                if command_type in ('ASYNC_RESP', 'EVENT_WAIT_RESP'):
                    # If this is a branch response, merge payload into the map item (both data.current_map_item and context map)
                    if is_branch_response and branch_key:
                        # Merge payload into current_map_item and persist to context map for continuity
                        existing_item = (current_state.values.get('data', {}) or {}).get('current_map_item')
                        if not existing_item:
                            existing_item = (current_state.values.get('context', {}).get('map_items_by_key', {}) or {}).get(branch_key) or {}
                        merged_item = self._deep_merge_dict(existing_item, payload or {})
                        # Persist to both data and context maps on the active thread
                        graph.update_state(config, {"data": {"current_map_item": merged_item}})
                        graph.update_state(config, {"context": {"map_items_by_key": {branch_key: merged_item}}})
                        # Also persist to the parent thread's context map so subsequent responses can restore state
                        try:
                            if 'parent_config' in locals() and isinstance(parent_config, dict):
                                graph.update_state(parent_config, {"context": {"map_items_by_key": {branch_key: merged_item}}})
                        except Exception:
                            # Non-fatal; continue even if parent update fails
                            pass

                    # Re-read current state after updates so event publication reflects merged data
                    current_state = graph.get_state(config)

                    # Prepare publication if applicable
                    current_step_payload = {
                        "name": interrupted_node,
                        "title": node_def.get("title", ""),
                        "type": node_def.get("type", ""),
                    }
                    next_steps_payload = compute_next_steps(transition_node, definition)
                    event_state = current_state.values.copy()
                    # Reflect merged data and status in event publication
                    event_state['data'] = self._deep_merge_dict(event_state.get('data', {}), payload or {})
                    event_state['data']['status'] = command_status

                    status_label = "Ended:AsyncRequest" if command_type == 'ASYNC_RESP' else "Ended:EventWait"
                    self.event_publisher.publish_event(
                        state=event_state,
                        current_step=current_step_payload,
                        next_steps=next_steps_payload,
                        status=status_label
                    )
                # --- ---


                # Merge the command's status and payload into the state's data channel
                # This makes the status available for condition nodes
                update_data = (payload or {}).copy()
                update_data['status'] = command_status
                # Deep merge into existing data to avoid overwriting prior context
                latest_state = graph.get_state(config)
                merged_data = self._deep_merge_dict(latest_state.values.get('data', {}) or {}, update_data)
                graph.update_state(config, {"data": merged_data}, as_node=transition_node)
                final_state = graph.invoke(None, config)
                adapter.info(f"Successfully resumed and ran workflow to completion. Final state: {final_state}")

                # If we are transitioning into a map_fork, attempt a drain-run ONLY if not currently interrupted.
                transitioned_node_def = definition.get("nodes", {}).get(transition_node, {})
                if transitioned_node_def.get("type") == "map_fork":
                    try:
                        self._drain_parallel_fanout(graph, config, adapter, definition, transition_node)
                    except Exception as drain_err:
                        adapter.warning(f"Drain-run after map_fork transition encountered an issue: {drain_err}")

            else:
                adapter.warning(f"Unknown command type '{command_type}' cannot be processed.")

        except Exception as e:
            adapter.error(f"Error processing command for workflow '{instance_id}': {e}", exc_info=True)

    def _drain_parallel_fanout(self, graph, config: dict, adapter: logging.LoggerAdapter, definition: Dict[str, Any], map_fork_node_name: str):
        """
        Dispatch async requests for each map_fork branch explicitly to avoid
        losing parallelism when running under a single-threaded Lambda invoke.
        Uses the workflow definition to build messages identical to the
        branch entry node (async_request) and marks branches as processed to
        ensure idempotency.
        """
        state_values = graph.get_state(config).values
        data = state_values.get("data", {})
        context = state_values.get("context", {})

        fork_def = definition.get("nodes", {}).get(map_fork_node_name, {})
        input_list_key = fork_def.get("input_list_key")
        branch_key_prop = fork_def.get("branch_key")
        entry_node_name = fork_def.get("branch_entry_node")
        entry_node_def = definition.get("nodes", {}).get(entry_node_name, {})

        if not all([input_list_key, branch_key_prop, entry_node_name, entry_node_def]):
            adapter.error("map_fork configuration incomplete; cannot drain-run fanout.")
            return

        items = data.get(input_list_key) or []
        if not isinstance(items, list):
            adapter.error(f"map_fork input_list_key '{input_list_key}' is not a list; aborting drain-run.")
            return

        processed = (context.get("processed_branch_requests", {}).get(map_fork_node_name)) or {}

        from utils.command_parser import CommandParser
        from clients.queue_client import QueueClient

        # If one branch request already executed during this invoke, it will be the
        # current_map_item in data; persist it and skip re-dispatching for that branch to avoid duplicates.
        in_flight_branch_id = (data.get('current_map_item') or {}).get('filingPackId')
        in_flight_item = data.get('current_map_item') if in_flight_branch_id else None

        # Persist the in-flight branch item if it exists so later responses can restore context
        if in_flight_item and in_flight_branch_id:
            graph.update_state(config, {"context": {"map_items_by_key": {in_flight_branch_id: in_flight_item}}})

        for item in items:
            branch_key = item.get(branch_key_prop)
            if not branch_key:
                continue
            if processed.get(branch_key):
                adapter.info(f"Branch '{branch_key}' already dispatched; skipping.")
                continue
            if in_flight_branch_id and branch_key == in_flight_branch_id:
                adapter.info(f"Branch '{branch_key}' already in-flight from main execution; skipping drain-run dispatch.")
                continue

            # Persist under context.map_items_by_key so response routing can restore context
            graph.update_state(config, {"context": {"map_items_by_key": {branch_key: item}}})

            temp_state = {
                "context": {**context, "branch_key": branch_key},
                "data": {**data, "current_map_item": item},
            }
            parser = CommandParser(temp_state, entry_node_def)
            full_command_message = parser.create_command_message(command_type="ASYNC_REQ")

            capability_id = entry_node_def.get("capability_id", "")
            service_name = capability_id.split('#')[0].upper()
            capability_key = f"CCH_CAPABILITY_{service_name}"
            queue_url = os.environ.get(capability_key) or os.environ.get('FALLBACK_CAPABILITY_QUEUE_URL')
            if not queue_url:
                adapter.error(f"No capability queue configured for service '{service_name}'.")
                continue

            QueueClient().send_message(queue_url, full_command_message)
            adapter.info(f"Drain-run dispatched async request for branch '{branch_key}' to '{queue_url}'.")

            graph.update_state(config, {"context": {"processed_branch_requests": {map_fork_node_name: {branch_key: True}}}})

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

    
