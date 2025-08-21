from collections import deque
from typing import Dict, List, Set


def compute_next_steps(
    start_node_name: str,
    definition: Dict,
    include_types: Set[str] | None = None,
) -> List[Dict]:
    """
    Breadth-first traversal from start_node_name following success-like edges to
    identify the first frontier of significant nodes. Significant nodes are those
    whose type is in include_types.

    Special handling:
    - condition: enqueue all branch destinations
    - event_wait: prefer on_event if present, else on_success
    - map_fork: include the map_fork itself, and enqueue its branch_entry_node
    - end_branch: treat as significant when included; traversal beyond requires
      join resolution which is handled by the caller if needed
    """
    if include_types is None:
        include_types = {"async_request", "event_wait", "map_fork", "end", "end_branch"}

    results: List[Dict] = []
    seen_names: Set[str] = set()
    queue: deque[str] = deque([start_node_name])
    visited: Set[str] = set()

    while queue:
        node_name = queue.popleft()
        if node_name in visited:
            continue
        visited.add(node_name)

        node_def = (definition.get("nodes", {}) or {}).get(node_name, {})
        node_type = node_def.get("type")

        if node_type in include_types:
            if node_name not in seen_names:
                results.append({
                    "name": node_name,
                    "title": node_def.get("title", ""),
                    "type": node_type,
                })
                seen_names.add(node_name)
            # Do not traverse beyond a significant node on this path
            continue

        if node_type == "condition":
            for dest in (node_def.get("branches", {}) or {}).values():
                if dest:
                    queue.append(dest)
            continue

        if node_type == "event_wait":
            dest = node_def.get("on_event") or node_def.get("on_success")
            if dest:
                queue.append(dest)
            continue

        if node_type == "map_fork":
            # Include the map_fork itself as a significant structural step
            if node_name not in seen_names and "map_fork" in include_types:
                results.append({
                    "name": node_name,
                    "title": node_def.get("title", ""),
                    "type": node_type,
                })
                seen_names.add(node_name)
            branch_entry = node_def.get("branch_entry_node")
            if branch_entry:
                queue.append(branch_entry)
            continue

        # Default success-like edges
        successor = node_def.get("on_success") or node_def.get("on_response")
        if successor:
            queue.append(successor)

    # Deduplicate by name while preserving order already guaranteed by seen_names check
    return results


