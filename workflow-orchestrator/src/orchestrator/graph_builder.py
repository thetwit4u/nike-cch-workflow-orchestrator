import logging
from functools import partial
from typing import Dict, Any
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Send
from langgraph.errors import GraphInterrupt

from orchestrator.state import WorkflowState
from orchestrator.nodes import core_nodes, capability_nodes, library_nodes

logger = logging.getLogger(__name__)

# A map of library function IDs to their corresponding handler functions
library_handler_map = {
    's3#read_jsonpath': library_nodes.handle_s3_read_jsonpath,
    'core#calculate_timedelta': library_nodes.handle_core_calculate_timedelta,
}


class GraphBuilder:
    """
    Compiles a workflow definition from a dictionary into an executable LangGraph object.
    """

    def __init__(self, definition: Dict[str, Any], checkpointer: BaseCheckpointSaver, event_publisher: Any):
        self.definition = definition
        self.checkpointer = checkpointer
        self.event_publisher = event_publisher
        self.workflow = StateGraph(WorkflowState)

    def compile_graph(self):
        """
        Compiles the workflow definition into an executable LangGraph object.
        """
        self._add_nodes()
        self._add_edges()
        self.workflow.set_entry_point(self.definition['entry_point'])
        
        return self.workflow.compile(checkpointer=self.checkpointer)

    def _find_join_node_for_map_fork(self, map_fork_node_name: str) -> str | None:
        """Finds the join node that is connected to a given map_fork node."""
        for node_name, node_data in self.definition['nodes'].items():
            if node_data.get('type') == 'join':
                if map_fork_node_name in node_data.get('join_branches', []):
                    return node_name
        return None

    def _add_nodes(self):
        """
        Adds all nodes from the definition to the graph.
        """
        for node_name, node_data in self.definition['nodes'].items():
            action = self._create_node_action(node_name, node_data)
            self.workflow.add_node(node_name, action)

    def _add_edges(self):
        """Adds edges to the graph based on the workflow definition."""
        for node_name, node_config in self.definition["nodes"].items():
            # The 'on_failure' edge is now handled globally for all node types that support it.
            # This simplifies the logic and ensures consistent error handling.
            if "on_failure" in node_config:
                self.workflow.add_conditional_edges(
                    node_name,
                    lambda state: "on_failure" if state.get("is_error") else "on_success",
                    {
                        "on_failure": node_config["on_failure"],
                        "on_success": self._get_success_path(node_config) or END
                    }
                )
            # For nodes without a dedicated failure path, we use standard edges.
            else:
                node_type = node_config.get("type")
                if node_type == "condition":
                        # Conditional nodes resolve their own branches; no standard edge needed here.
                        self._add_conditional_edge(node_name, node_config)
                elif node_type == "map_fork":
                        # Map_fork has its own resolver for parallel execution.
                        self._add_map_fork_edge(node_name, node_config)
                elif node_type == "event_wait":
                    self._add_event_wait_edge(node_name, node_config)
                else:
                    # For all other nodes, add a direct edge to their success path.
                    success_path = self._get_success_path(node_config)
                    if success_path:
                        self.workflow.add_edge(node_name, success_path)
                
    def _get_success_path(self, node_config: dict) -> str | None:
        """Helper to get the primary success transition for a node."""
        return node_config.get("on_success") or \
               node_config.get("on_response") or \
               node_config.get("on_event")

    def _add_conditional_edge(self, node_name: str, node_data: Dict[str, Any]):
        """Adds a standard conditional edge for branching."""
        branches = node_data.get('branches', {})
        on_failure_node = node_data.get('on_failure')

        # The path map for langgraph needs to map from the *value returned by the resolver*
        # to the destination node name. Since our resolver returns the destination node name
        # directly, the keys and values of the path_map should be the same.
        destinations = list(branches.values())
        path_map = {dest: dest for dest in destinations}

        # If an on_failure node is defined, add it to the path map with a special key
        if on_failure_node:
            path_map['__failure__'] = on_failure_node
        
        self.workflow.add_conditional_edges(
            node_name,
            self._create_branch_resolver(
                node_name=node_name,
                condition_key=node_data.get('condition_on_key'),
                branches=branches, # The resolver still needs the original logic
                has_failure_path=bool(on_failure_node)
            ),
            path_map # Pass the correctly structured map to langgraph
        )

    def _add_map_fork_edge(self, node_name: str, node_data: Dict[str, Any]):
        """
        Adds a conditional edge for a map_fork node that uses Send for parallelism.
        This also dynamically injects a registration node at the start of each branch.
        """
        branch_entry_node = node_data.get('branch_entry_node')
        if not branch_entry_node:
            raise ValueError(f"map_fork node '{node_name}' must have a 'branch_entry_node' property.")

        # Discover the join node dynamically instead of using a direct property
        join_node = self._find_join_node_for_map_fork(node_name)
        if not join_node:
            raise ValueError(f"Could not find a join node that lists '{node_name}' in its join_branches.")

        # Dynamically create and add the registration node for this specific map_fork
        registration_node_name = f"__internal_register_{node_name}"
        registration_action = partial(
            core_nodes.handle_register_branch, 
            checkpointer=self.checkpointer
        )
        self.workflow.add_node(registration_node_name, registration_action)
        self.workflow.add_edge(registration_node_name, branch_entry_node)

        def map_resolver(state: WorkflowState):
            map_on_key = node_data.get("input_list_key")
            if not map_on_key:
                raise ValueError(f"map_fork node '{node_name}' is missing 'input_list_key'")
            
            items_to_map = state["data"].get(map_on_key, [])
            if not isinstance(items_to_map, list):
                raise TypeError(f"Key '{map_on_key}' for map_fork must be a list in state['data'].")
            
            logger.info(f"Dispatching {len(items_to_map)} parallel tasks from '{node_name}' to '{registration_node_name}'.")
            
            branch_key_property = node_data.get('branch_key')
            if not branch_key_property:
                raise ValueError(f"map_fork node '{node_name}' is missing 'branch_key' property.")

            parent_thread_id = state['context']['workflowInstanceId']

            sends = []
            for item in items_to_map:
                branch_key = item.get(branch_key_property)
                if not branch_key:
                    logger.warning(f"Item in list '{map_on_key}' is missing required branch key property '{branch_key_property}'. Skipping.")
                    continue

                item_key = node_data.get('item_key', 'current_map_item')
                # The state for each parallel branch is minimal and contains routing info.
                branch_state = {
                    "context": {"parent_thread_id": parent_thread_id, "branch_key": branch_key},
                    "data": {item_key: item}
                }
                sends.append(Send(registration_node_name, branch_state))
            
            return sends

        self.workflow.add_conditional_edges(
            node_name,
            map_resolver,
            then=join_node
        )

    def _add_event_wait_edge(self, node_name: str, node_data: Dict[str, Any]):
        """
        This is now handled by the node's action returning an Interrupt.
        The resume logic will be handled by the service layer.
        This function can be removed or simplified as it's no longer creating edges.
        """
        # This function is now effectively a no-op as the edge logic is implicit.
        # The 'on_success' edge is added via the default edge handler.
        pass


    def _create_branch_resolver(self, node_name: str, condition_key: str, branches: Dict[str, str], has_failure_path: bool):
        """
        Creates a resolver function for a conditional node that determines the next branch.
        """
        def resolve_branch(state: WorkflowState) -> str:
            if state.get("is_error", False) and has_failure_path:
                logger.warning(f"Error detected in state. Routing node '{node_name}' to on_failure.")
                return "__failure__"
            
            # All condition keys are resolved against the 'data' field of the state.
            data_context = state.get("data", {})
            
            # Allow nested key access within the data context, e.g., "importFilingPacksError.error_type"
            keys = condition_key.split('.')
            value = data_context
            try:
                for key in keys:
                    value = value[key]
            except (KeyError, TypeError):
                logger.warning(f"Condition key '{condition_key}' not found in state's data context. Defaulting to None.")
                value = None # Key not found, treat as None

            logger.info(f"Condition node '{node_name}' resolving based on key 'data.{condition_key}' with value '{value}'")
            
            # Note: The branch keys in the YAML must be strings. 'True' becomes "True".
            branch_target = branches.get(str(value), branches.get("_default"))
            if branch_target:
                return branch_target

            if has_failure_path:
                logger.warning(f"No direct branch found for value '{value}' in node '{node_name}'. Routing to on_failure.")
                return "__failure__"
            
            raise ValueError(f"No branch found for value '{value}' in condition node '{node_name}', and no 'on_failure' or '_default' is defined.")
        
        return resolve_branch

    def _create_node_action(self, node_name: str, node_data: Dict[str, Any]):
        """
        Factory function to create the appropriate node action based on its type.
        This now returns a wrapper that handles global concerns like error handling
        and updating the current_node context.
        """
        
        # This inner function gets the specific business logic for the node type.
        base_action = self._get_base_action(node_data.get("type"), node_name, node_data)

        def action_wrapper(state: WorkflowState):
            # Always update the current node in the context first.
            state["context"]["current_node"] = node_name
            
            # The global error handler is now implemented as a try/except block here.
            # This ensures that any node can be routed to its 'on_failure' path.
            try:
                # Execute the specific action for the node
                result = base_action(state)

                # If the action returns an Interrupt, it must be re-raised to be
                # caught by LangGraph's machinery. Do not wrap it.
                if isinstance(result, GraphInterrupt):
                    return result

                # On successful execution, clear any previous error state.
                # This is important for retry scenarios.
                if state.get("is_error"):
                    if isinstance(result, dict):
                        result.setdefault("is_error", False)
                        result.setdefault("error_details", None)
                    else: # If the result is not a dict, we can't patch it. This shouldn't happen with our actions.
                        logger.warning(f"Node '{node_name}' returned non-dict result; cannot clear error state.")

                return result

            except GraphInterrupt:
                # This is a special exception used by LangGraph to pause execution.
                # It should be re-raised immediately without being caught as a general error.
                raise
            except Exception as e:
                error_message = f"Unhandled exception in node '{node_name}': {e}"
                logger.error(error_message, exc_info=True)
                # This is a critical failure. We update the state to reflect the error
                # and allow the conditional edge logic to route to 'on_failure'.
                return {
                    "is_error": True,
                    "error_details": {"node": node_name, "error": error_message}
                }
        
        return action_wrapper


    def _get_base_action(self, node_type: str, node_name: str, node_data: dict):
        """
        Returns the specific handler function for a given node type.
        """
        if node_type == "start":
            return partial(core_nodes.handle_start_node, node_config=node_data, node_name=node_name)
        elif node_type == "end" or node_type == "set_state":
            # 'end' is just a 'set_state' with a conventional name
            return partial(core_nodes.set_state_node_wrapper, node_config=node_data, node_name=node_name)
        elif node_type == "log_error":
            return partial(core_nodes.handle_log_error, node_config=node_data, node_name=node_name)
        elif node_type == 'event_wait':
             return partial(core_nodes.handle_event_wait, node_config=node_data, node_name=node_name)
        elif node_type == "async_request":
            return partial(capability_nodes.handle_async_request, node_config=node_data, node_name=node_name, event_publisher=self.event_publisher, workflow_definition=self.definition)
        elif node_type == "sync_call":
            return partial(capability_nodes.handle_sync_call, node_config=node_data, node_name=node_name)
        elif node_type == "scheduled_request":
            return partial(capability_nodes.handle_scheduled_request, node_config=node_data, node_name=node_name)
        elif node_type == "library_function":
            handler = library_handler_map.get(node_data.get('function_id'))
            if not handler:
                raise ValueError(f"No handler found for library function_id: {node_data.get('function_id')}")
            # Wrap the specific library handler in the generic wrapper
            return partial(library_nodes.library_node_wrapper, handler=handler, node_config=node_data, node_name=node_name)
        elif node_type == 'condition' or node_type == 'fork' or node_type == 'join' or node_type == 'map_fork':
            # These nodes only have routing logic defined in edges, so their action is a no-op.
            return core_nodes.pass_through_action
        else:
            def unhandled_node_action(state):
                raise NotImplementedError(f"Node type '{node_type}' is not yet implemented.")
            return unhandled_node_action 
