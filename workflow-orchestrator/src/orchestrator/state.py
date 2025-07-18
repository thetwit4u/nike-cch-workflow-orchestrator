from typing import TypedDict, Dict, Any, Optional, Annotated
import operator

def _overwrite_dict(left: dict, right: dict) -> dict:
    """A reducer that merges two dictionaries, with the right side overwriting left."""
    # This is not a deep merge. It's a shallow update.
    # We create a new dictionary to avoid mutating the original state objects.
    merged = (left or {}).copy()
    if right:
        merged.update(right)
    return merged

def _merge_branch_checkpoints(left: dict, right: dict) -> dict:
    """This reducer is specifically for merging branch checkpoints. It should not be used for general state."""
    # Note: This is a placeholder. A real implementation might need more
    # sophisticated logic to handle complex merge scenarios between parallel branches.
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
    context: Annotated[Dict[str, Any], _overwrite_dict]
    command: dict[str, Any]
    data: Annotated[Dict[str, Any], _overwrite_dict]
    # This key is used to collect results from parallel map_fork branches
    map_results: Annotated[list, operator.add]
    # This key stores the mapping of business branch_key to internal thread_id
    branch_checkpoints: Annotated[Dict[str, Any], _merge_branch_checkpoints]
    current_operation: dict[str, Any]
    is_error: Annotated[bool, operator.or_]
    error_details: Annotated[Optional[dict[str, Any]], _overwrite_dict] 