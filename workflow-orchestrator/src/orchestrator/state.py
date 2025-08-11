from typing import TypedDict, Dict, Any, Optional, Annotated
import operator

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
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged

def _merge_branch_checkpoints(left: dict, right: dict) -> dict:
    """This reducer is specifically for merging branch checkpoints. It should not be used for general state."""
    # Note: This is a placeholder. A real implementation might need more
    # sophisticated logic to handle complex merge scenarios between parallel branches.
    return {**(left or {}), **(right or {})}

class WorkflowState(TypedDict):
    """
    Represents the state of a single workflow instance that is passed between nodes in the graph.
    
    **Developer Guidance:**
    
    While a full refactoring is planned, please adhere to the following conventions to ensure consistency:
    
    - **`context`**: Use this field to store long-lived business data and core workflow identifiers.
        - **Examples:** `workflowInstanceId`, `correlationId`, `consignmentId`.
        - **Behavior:** Data in `context` should persist and be accessible throughout the entire workflow.
        
    - **`data`**: Use this field primarily for the transient inputs and outputs of individual nodes.
        - **Examples:** The result of a capability call, the payload for a condition node.
        - **Behavior:** The `data` field is often overwritten by the output of a node. Do not store critical, long-lived data here.

    """
    workflow_definition_uri: str
    context: Annotated[Dict[str, Any], _deep_merge_dict]
    command: dict[str, Any]
    data: Annotated[Dict[str, Any], _deep_merge_dict]
    # This key is used to collect results from parallel map_fork branches
    map_results: Annotated[list, operator.add]
    # This key stores the mapping of business branch_key to internal thread_id
    branch_checkpoints: Annotated[Dict[str, Any], _merge_branch_checkpoints]
    current_operation: dict[str, Any]
    is_error: Annotated[bool, operator.or_]
    error_details: Annotated[Optional[dict[str, Any]], _deep_merge_dict] 