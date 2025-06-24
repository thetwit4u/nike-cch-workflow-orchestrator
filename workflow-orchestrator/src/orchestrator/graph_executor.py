import logging
from typing import Dict, Any, TypedDict
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from threading import Lock
import tempfile
import os
from langgraph.types import Command, Interrupt

from orchestrator.nodes import capability_nodes
from clients.s3_client import S3Client
from orchestrator.nodes import core_nodes, capability_nodes

logger = logging.getLogger(__name__)

class WorkflowState(TypedDict):
    """Represents the state of a single workflow instance."""
    workflow_definition_uri: str
    context: dict
    
class GraphExecutor:
    """
    Compiles and executes a workflow definition using LangGraph.
    """
    _instance = None
    _lock = Lock()

    def __init__(self, service):
        from orchestrator.service import OrchestratorService
        if not isinstance(service, OrchestratorService):
            raise TypeError("Service must be an instance of OrchestratorService")
        self.service = service
        self.state_saver = service.state_saver
        # Cache for compiled graphs to avoid recompiling the same definition
        self.compiled_graphs: Dict[str, Any] = {}
        logger.info("GraphExecutor initialized.")

    def _get_or_compile_graph(self, definition: Dict[str, Any]):
        """
        Retrieves a compiled graph from cache or compiles it if not found.
        """
        workflow_id = definition.get("workflow_id")
        if not workflow_id:
            raise ValueError("Workflow definition must have a 'workflow_id'.")

        if workflow_id not in self.compiled_graphs:
            logger.info(f"Compiling new graph for workflow_id: {workflow_id}")
            graph = self._compile_graph(definition)
            self.compiled_graphs[workflow_id] = graph
        return self.compiled_graphs[workflow_id]

    def start_workflow(self, workflowInstanceId: str, definition: Dict[str, Any], initial_payload: Dict[str, Any], workflow_uri: str):
        """
        Starts a new workflow instance from the beginning.
        """
        logger.info(f"[WorkflowID: {workflowInstanceId}] Starting workflow with initial payload.")
        
        graph = self._get_or_compile_graph(definition)

        # Log the workflow graph as a Mermaid diagram as a single JSON-escaped string with the correct workflowInstanceId
        try:
            mermaid_str = graph.get_graph().draw_mermaid()
            import json
            diagram_json = json.dumps({"diagram": mermaid_str})
            logger.info(f"[WorkflowID: {workflowInstanceId}] [MERMAID_DIAGRAM] {diagram_json}")
        except Exception as e:
            logger.warning(f"Failed to generate Mermaid diagram for workflow graph: {e}")
        
        # Add the workflow_id and workflowInstanceId to the context so it's available to all nodes
        initial_payload['workflow_id'] = definition.get("workflow_id")
        initial_payload['workflowInstanceId'] = workflowInstanceId
        # For LangGraph compatibility, set __thread_id
        initial_payload['__thread_id'] = workflowInstanceId
        initial_state = {
            "workflow_definition_uri": workflow_uri,
            "context": initial_payload
        }
        
        # The 'configurable' dict is how we pass instance-specific info to langgraph
        config = {"configurable": {"thread_id": workflowInstanceId}}
        
        # Invoke the graph. LangGraph handles the state saving automatically.
        graph.invoke(initial_state, config)
        
        logger.info(f"[WorkflowID: {workflowInstanceId}] Workflow has started and is now either paused or finished.")


    def resume_workflow(self, workflowInstanceId: str, workflow_definition_uri: str, response_payload: Dict[str, Any]):
        """
        Resumes a paused workflow instance with new data from an async response.
        This method is designed to be stateless.
        """
        logger.info(f"[WorkflowID: {workflowInstanceId}] Resuming workflow with new data: {response_payload}")

        if not workflow_definition_uri:
            logger.error(f"[WorkflowID: {workflowInstanceId}] Cannot resume workflow: 'workflow_definition_uri' was not provided in the async response.")
            return
        
        # S3 client to re-fetch the definition if needed
        s3_client = S3Client()
        definition = s3_client.get_workflow_definition(workflow_definition_uri)
            
        # Get the compiled graph, re-compiling if it's not in the in-memory cache
        graph = self._get_or_compile_graph(definition)

        config = {"configurable": {"thread_id": workflowInstanceId}}
        
        # Get the current state and update it with the new data.
        current_state = graph.get_state(config)
        current_values = current_state.values
        old_context = current_values.get("context", {})
        logger.info(f"[WorkflowID: {workflowInstanceId}] [resume_workflow] Old context before merge: {old_context}")
        # Merge response_payload into old_context, but keep required fields
        updated_context = {**old_context, **response_payload}
        updated_context["workflow_id"] = definition.get("workflow_id")
        updated_context["workflowInstanceId"] = workflowInstanceId
        updated_context["__thread_id"] = workflowInstanceId  # For LangGraph compatibility
        logger.info(f"[WorkflowID: {workflowInstanceId}] [resume_workflow] Updated context after merge: {updated_context}")
        
        # Update the state in the checkpointer.
        graph.update_state(config, {"context": updated_context})
        
        # Now, invoke the graph to continue from where it was, using Command(resume=...)
        result = graph.invoke(Command(resume=response_payload), config)
        if isinstance(result, Interrupt):
            logger.info(f"[WorkflowID: {workflowInstanceId}] [resume_workflow] Graph returned an Interrupt: {result}")
        else:
            logger.info(f"[WorkflowID: {workflowInstanceId}] [resume_workflow] Graph resumed and returned: {result}")

        logger.info(f"[WorkflowID: {workflowInstanceId}] Successfully resumed workflow.")

    def _compile_graph(self, definition: Dict[str, Any]):
        """
        Compiles the workflow definition into an executable LangGraph object.
        """
        workflow = StateGraph(WorkflowState)
        
        # Add nodes
        for node_name, node_data in definition['nodes'].items():
            action = self._create_node_action(node_data)
            workflow.add_node(node_name, action)

        # Add edges
        for node_name, node_data in definition['nodes'].items():
            if node_data.get('type') == 'end':
                workflow.add_edge(node_name, END)
            
            if 'on_success' in node_data:
                workflow.add_edge(node_name, node_data['on_success'])
            
            if 'on_response' in node_data:
                workflow.add_edge(node_name, node_data['on_response'])
        
        workflow.set_entry_point(definition['entry_point'])
        
        # Compile the graph with the state saver attached
        return workflow.compile(checkpointer=self.state_saver)

    def _create_node_action(self, node_data: Dict[str, Any]):
        """
        A factory function that returns the appropriate callable action for a node.
        """
        node_type = node_data.get('type')
        
        if node_type == 'async_request':
            return capability_nodes.create_async_request_action(node_data)
        elif node_type == 'sync_call':
            return capability_nodes.create_sync_call_action(node_data)
        elif node_type == 'end':
            def end_action(state):
                context = state.get('context', {})
                workflowInstanceId = context.get('workflowInstanceId', 'unknown')
                logger.info(f"[orchestrator.graph_executor] [WorkflowID: {workflowInstanceId}] Workflow has ended.")
            return end_action
        else:
            raise ValueError(f"Unknown node type: '{node_type}'")

    @classmethod
    def get_instance(cls, service):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(service)
            return cls._instance