from typing import TypedDict, Dict, Any, List, Optional, Annotated
import operator

def _merge_branch_checkpoints(left: dict | None, right: dict | None) -> dict:
    """
    Merges two dictionaries, giving precedence to the right-hand side.
    Handles cases where one or both dictionaries might be None.
    """
    return {**(left or {}), **(right or {})}

class WorkflowState(TypedDict):
    """
    Represents the state of a single workflow instance that is passed between nodes in the graph.
    
    Attributes:
        workflow_definition_uri: The S3 URI of the workflow definition YAML.
        context: A dictionary holding all the business data, results from nodes,
                 and control flags for the workflow.
    """
    workflow_definition_uri: str
    context: Annotated[Dict, _merge_branch_checkpoints]
    command: dict[str, Any]
    data: Annotated[dict, _merge_branch_checkpoints]
    # This key is used to collect results from parallel map_fork branches
    map_results: Annotated[list, operator.add]
    # This key stores the mapping of business branch_key to internal thread_id
    branch_checkpoints: Annotated[dict, _merge_branch_checkpoints]
    current_operation: dict[str, Any]
    is_error: Annotated[bool, operator.or_]
    error_details: Annotated[Optional[dict[str, Any]], _merge_branch_checkpoints] 