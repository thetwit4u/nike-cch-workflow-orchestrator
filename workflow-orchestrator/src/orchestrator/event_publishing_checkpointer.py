import logging
from typing import Any, Dict, List, Tuple

from langgraph.checkpoint.base import (
    BaseCheckpointSaver, Checkpoint, CheckpointMetadata, ChannelVersions, CheckpointTuple
)
from langchain_core.runnables import RunnableConfig

from orchestrator.event_publisher import EventPublisher

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
            
            # Only publish for terminal or async nodes from the checkpointer
            if current_node_type in ["async_request", "end"]:
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
                
                logger.info(f"Publishing event for completed node: {current_node_name} (Type: {current_node_type}) -> Next: {[n['name'] for n in next_steps_payload]}")
                self.event_publisher.publish_event(state, current_step_payload, next_steps_payload)

        except Exception as e:
            logger.error(f"Error publishing event after saving checkpoint: {e}", exc_info=True)

        return result

    def put_writes(self, config: RunnableConfig, writes: List[Tuple[str, Any]], task_id: str) -> None:
        return self.saver.put_writes(config, writes, task_id)

    async def aput_writes(self, config: RunnableConfig, writes: List[Tuple[str, Any]], task_id: str) -> None:
        return await self.saver.aput_writes(config, writes, task_id)
