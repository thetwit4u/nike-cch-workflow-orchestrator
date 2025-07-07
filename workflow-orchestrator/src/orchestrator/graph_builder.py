import logging
from functools import partial
from typing import Dict, Any
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Send

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

    def __init__(self, definition: Dict[str, Any], checkpointer: BaseCheckpointSaver):
        self.definition = definition
        self.checkpointer = checkpointer
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
            node_type = node_config.get("type")

            if node_type == "condition":
                branches = node_config.get("branches", {})
                for next_node in branches.values():
                    if next_node:  # Ensure there's a next node defined
                        self.workflow.add_edge(node_name, next_node)
            elif node_type == "map_fork":
                # The join node will have an edge pointing *from* the map_fork
                pass
            elif node_type == "join":
                # The join node is a convergence point, edges point *to* it
                if "on_success" in node_config:
                    self.workflow.add_edge(node_name, node_config["on_success"])
                if "on_failure" in node_config:
                    self.workflow.add_edge(node_name, node_config["on_failure"])
            else:  # For linear nodes like library_call, async_request, etc.
                # Handle both on_success and the on_response alias for async nodes
                success_path = node_config.get("on_success") or node_config.get("on_response")
                
                if success_path:
                    self.workflow.add_edge(node_name, success_path)
                
                if "on_failure" in node_config:
                    # Add a conditional edge for failure based on the 'is_error' flag
                    # The source of the conditional edge is the node itself
                    self.workflow.add_conditional_edges(
                        node_name,
                        # The decider function checks the 'is_error' flag in the state
                        lambda state: state.get("is_error", False),
                        {
                            # If is_error is True, go to the on_failure node
                            True: node_config["on_failure"],
                            # If is_error is False, go to success_path or end the graph
                            False: success_path or END
                        }
                    )

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
        Adds a conditional edge for an event_wait node. It pauses execution
        by routing to END if the event_key is not in the state.
        """
        event_key = node_data.get('event_key')
        if not event_key:
            raise ValueError(f"event_wait node '{node_name}' must have an 'event_key' property.")
        
        on_event_node = node_data.get('on_event')
        if not on_event_node:
            raise ValueError(f"event_wait node '{node_name}' must have an 'on_event' property.")

        def event_resolver(state: WorkflowState):
            if event_key in state['data']:
                logger.info(f"Event key '{event_key}' found. Resuming from '{node_name}' to '{on_event_node}'.")
                return on_event_node
            else:
                logger.info(f"Event key '{event_key}' not found. Pausing execution at '{node_name}'.")
                return END
        
        # This is a special conditional edge that only has one real path.
        # The path map directs the resolved node name to itself.
        self.workflow.add_conditional_edges(
            node_name,
            event_resolver,
            {on_event_node: on_event_node}
        )

    def _create_branch_resolver(self, node_name: str, condition_key: str, branches: Dict[str, str], has_failure_path: bool):
        """
        Creates a resolver function for a conditional node that determines the next branch.
        """
        def resolve_branch(state: WorkflowState) -> str:
            if state.get("is_error", False) and has_failure_path:
                logger.warning(f"Error detected in state. Routing node '{node_name}' to on_failure.")
                return "__failure__"
            
            # Allow nested key access, e.g., "context.some_key"
            keys = condition_key.split('.')
            value = state
            try:
                for key in keys:
                    value = value[key]
            except (KeyError, TypeError):
                value = None # Key not found, treat as None

            logger.info(f"Condition node '{node_name}' resolving based on key '{condition_key}' with value '{value}'")
            
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
        A factory function that returns the appropriate callable action for a node.
        """
        node_type = node_data.get('type')
        library_call = node_data.get('library_function_id')
        capability_call = node_data.get('capability_id')

        # --- Core Node Types ---
        if node_type == 'end':
            return partial(core_nodes.set_state_node_wrapper,
                           node_config={'static_outputs': {'status': 'COMPLETED'}},
                           node_name=node_name)

        if node_type in ['entry', 'fork', 'join', 'condition', 'map_fork', 'event_wait', 'end_branch']:
             return lambda state: state # These are routing/structural nodes

        if node_type == 'log_error':
            return partial(core_nodes.handle_log_error, node_config=node_data, node_name=node_name)

        if node_type == 'set_state':
            return partial(core_nodes.set_state_node_wrapper, node_config=node_data, node_name=node_name)

        # --- Library Call Node ---
        if library_call:
            if library_call not in library_handler_map:
                raise ValueError(f"Unknown library function ID: {library_call}")
            handler = library_handler_map[library_call]
            return partial(handler, node_config=node_data, node_name=node_name)
        
        # --- Capability Node Types ---
        if node_type == 'async_request':
            if not capability_call:
                raise ValueError(f"Node '{node_name}' of type 'async_request' must have a 'capability_id'.")
            return partial(capability_nodes.handle_async_request, node_config=node_data, node_name=node_name)

        if node_type == 'scheduled_request':
            if not capability_call:
                raise ValueError(f"Node '{node_name}' of type 'scheduled_request' must have a 'capability_id'.")
            return partial(capability_nodes.handle_scheduled_request, node_config=node_data, node_name=node_name)
        
        if node_type == 'sync_call':
            if not capability_call:
                raise ValueError(f"Node '{node_name}' of type 'sync_call' must have a 'capability_id'.")
            return partial(capability_nodes.handle_sync_call, node_config=node_data, node_name=node_name)

        # --- Fallback for unhandled node types ---
        logger.warning(f"No specific action found for node '{node_name}' of type '{node_type}'. It will be treated as a pass-through node.")
        
        def unhandled_node_action(state):
            return state
        return unhandled_node_action 