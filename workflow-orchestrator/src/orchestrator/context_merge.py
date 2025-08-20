from typing import Any, Dict


def _deep_merge_dict(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deeply merge two dictionaries. Nested dictionaries are merged recursively;
    non-dict values are overwritten by values from 'right'. Returns a new dict.
    """
    if not isinstance(left, dict):
        left = {}
    if not isinstance(right, dict):
        return dict(left)

    merged: Dict[str, Any] = dict(left)
    for key, value in right.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def merge_branch_context(business_ctx: Dict[str, Any], branch_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge branch context into business context for event publication.

    Rules:
    - Exclude internal keys: any key starting with '_' and 'current_map_item' are not merged
    - Everything else is merged using deep merge to avoid losing nested context
    - Input dicts are not mutated; a new dict is returned
    """
    result: Dict[str, Any] = dict(business_ctx or {})
    if not isinstance(branch_ctx, dict):
        return result

    filtered_branch: Dict[str, Any] = {}
    for key, value in branch_ctx.items():
        if key == "current_map_item" or (isinstance(key, str) and key.startswith("_")):
            continue
        filtered_branch[key] = value

    return _deep_merge_dict(result, filtered_branch)


