import logging
from typing import Any, Dict, List, Tuple

from langgraph.checkpoint.base import (
    BaseCheckpointSaver, Checkpoint, CheckpointMetadata, ChannelVersions, CheckpointTuple
)
from langchain_core.runnables import RunnableConfig

from orchestrator.event_publisher import EventPublisher
from orchestrator.next_steps import compute_next_steps

logger = logging.getLogger(__name__)

class EventPublishingCheckpointer(BaseCheckpointSaver):
    """
    A wrapper around a checkpoint saver that publishes events after a checkpoint is saved.
    This class ensures that events are published only once per checkpoint and handles
    the full BaseCheckpointSaver interface.
    """

    def __init__(self, saver: BaseCheckpointSaver, event_publisher: EventPublisher, workflow_definition: Dict[str, Any]):
        super().__init__(serde=saver.serde)
        self.saver = saver
        self.event_publisher = event_publisher
        self.workflow_definition = workflow_definition
        self._published_checkpoint_ids = set()

    def get(self, config: RunnableConfig) -> Checkpoint | None:
        return self.saver.get(config)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.saver.get_tuple(config)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await self.saver.aget_tuple(config)

    def list(self, config: RunnableConfig) -> list[Checkpoint]:
        return self.saver.list(config)

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        # First, save the checkpoint using the underlying saver.
        result = self.saver.put(config, checkpoint, metadata, new_versions)

        checkpoint_id = checkpoint.get("id")
        if not checkpoint_id or checkpoint_id in self._published_checkpoint_ids:
            return result

        # If the save was successful, then publish the event.
        try:
            self._published_checkpoint_ids.add(checkpoint_id)
            state = checkpoint["channel_values"]
            
            current_node_name = state.get("context", {}).get("current_node")

            if not current_node_name:
                return result # Nothing to do if there's no current node

            current_node_def = self.workflow_definition.get("nodes", {}).get(current_node_name, {})
            current_node_type = current_node_def.get("type")
            
            # Only publish for specific node types from the checkpointer
            if current_node_type in ["end"]:
                status = "Ended"

                next_nodes = checkpoint.get("next", ())
                current_step_payload = {
                    "name": current_node_name,
                    "title": current_node_def.get("title", ""),
                    "type": current_node_type,
                }

                next_steps_payload = []
                for next_node_name in next_nodes:
                    next_node_def = self.workflow_definition.get("nodes", {}).get(next_node_name, {})
                    next_steps_payload.append({
                        "name": next_node_name,
                        "title": next_node_def.get("title", ""),
                        "type": next_node_def.get("type", ""),
                    })
                
                logger.info(
                    f"TRACE:STEPS current={{'name': '{current_step_payload['name']}', 'type': '{current_step_payload['type']}'}} "
                    f"next={[n['name'] for n in next_steps_payload]} status={status}"
                )
                self.event_publisher.publish_event(state, current_step_payload, next_steps_payload, status, self.workflow_definition)
            elif current_node_type == "event_wait":
                status = "Started:EventWait"

                # Determine the traversal start from on_event if present, else on_success
                start_from = current_node_def.get("on_event") or current_node_def.get("on_success")

                current_step_payload = {
                    "name": current_node_name,
                    "title": current_node_def.get("title", ""),
                    "type": current_node_type,
                }
                next_steps_payload = compute_next_steps(start_from, self.workflow_definition) if start_from else []

                logger.info(
                    f"TRACE:STEPS current={{'name': '{current_step_payload['name']}', 'type': '{current_step_payload['type']}'}} "
                    f"next={[n['name'] for n in next_steps_payload]} status={status}"
                )
                self.event_publisher.publish_event(state, current_step_payload, next_steps_payload, status, self.workflow_definition)
            elif current_node_type == "map_fork":
                status = "Started:MapFork"

                current_step_payload = {
                    "name": current_node_name,
                    "title": current_node_def.get("title", ""),
                    "type": current_node_type,
                }
                # Compute from the map_fork node itself to include structural next steps
                next_steps_payload = compute_next_steps(current_node_name, self.workflow_definition)

                logger.info(
                    f"TRACE:STEPS current={{'name': '{current_step_payload['name']}', 'type': '{current_step_payload['type']}'}} "
                    f"next={[n['name'] for n in next_steps_payload]} status={status}"
                )
                self.event_publisher.publish_event(state, current_step_payload, next_steps_payload, status, self.workflow_definition)
            elif current_node_type == "end_branch":
                status = "Ended:Branch"

                # Try to resolve join(s). If exactly one join exists, traverse from it; else include joins directly.
                nodes_dict = (self.workflow_definition.get("nodes", {}) or {})
                join_nodes = [
                    (n, nd) for n, nd in nodes_dict.items() if nd.get("type") == "join"
                ]

                current_step_payload = {
                    "name": current_node_name,
                    "title": current_node_def.get("title", ""),
                    "type": current_node_type,
                }

                if len(join_nodes) == 1:
                    sole_join_name = join_nodes[0][0]
                    next_steps_payload = compute_next_steps(sole_join_name, self.workflow_definition)
                else:
                    # Narrow to joins whose join_branches contain a map_fork that can reach this end_branch
                    candidate_joins: list[tuple[str, dict]] = []
                    for join_name, join_def in join_nodes:
                        join_branches = join_def.get("join_branches") or []
                        if not isinstance(join_branches, list) or not join_branches:
                            continue
                        for map_fork_name in join_branches:
                            map_fork_def = nodes_dict.get(map_fork_name, {})
                            if map_fork_def.get("type") != "map_fork":
                                continue
                            entry = map_fork_def.get("branch_entry_node")
                            if entry and self._can_reach(entry, current_node_name):
                                candidate_joins.append((join_name, join_def))
                                break

                    # Deduplicate while preserving order
                    seen = set()
                    filtered = []
                    for n, nd in candidate_joins:
                        if n not in seen:
                            seen.add(n)
                            filtered.append((n, nd))

                    if filtered:
                        next_steps_payload = [
                            {"name": n, "title": nd.get("title", ""), "type": nd.get("type", "")}
                            for n, nd in filtered
                        ]
                    else:
                        # Fallback to all joins when no candidates matched
                        next_steps_payload = [
                            {"name": n, "title": nd.get("title", ""), "type": nd.get("type", "")}
                            for n, nd in join_nodes
                        ]

                logger.info(
                    f"TRACE:STEPS current={{'name': '{current_step_payload['name']}', 'type': '{current_step_payload['type']}'}} "
                    f"next={[n['name'] for n in next_steps_payload]} status={status}"
                )
                self.event_publisher.publish_event(state, current_step_payload, next_steps_payload, status, self.workflow_definition)

        except Exception as e:
            logger.error(f"Error publishing event after saving checkpoint: {e}", exc_info=True)

        return result

    def _can_reach(self, start_node_name: str, target_node_name: str) -> bool:
        """Check if target_node_name is reachable from start_node_name via success-like edges."""
        nodes = (self.workflow_definition.get("nodes", {}) or {})
        visited: set[str] = set()
        queue: list[str] = [start_node_name]
        while queue:
            node_name = queue.pop(0)
            if node_name in visited:
                continue
            visited.add(node_name)
            if node_name == target_node_name:
                return True
            node_def = nodes.get(node_name, {})
            node_type = node_def.get("type")
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
                branch_entry = node_def.get("branch_entry_node")
                if branch_entry:
                    queue.append(branch_entry)
                continue
            successor = node_def.get("on_success") or node_def.get("on_response")
            if successor:
                queue.append(successor)
        return False

    def put_writes(self, config: RunnableConfig, writes: List[Tuple[str, Any]], task_id: str) -> None:
        return self.saver.put_writes(config, writes, task_id)

    async def aput_writes(self, config: RunnableConfig, writes: List[Tuple[str, Any]], task_id: str) -> None:
        return await self.saver.aput_writes(config, writes, task_id)