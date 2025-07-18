import pytest
import logging
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

# Add the src directory to the path so we can import the service
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'workflow-orchestrator', 'src'))

from orchestrator.service import OrchestratorService

logger = logging.getLogger(__name__)


class TestIdempotencyGuardClauseUnit:
    """
    Unit tests for the idempotency guard clause implementation.
    
    These tests directly test the OrchestratorService.process_command method
    without requiring the full AWS infrastructure.
    """

    @pytest.fixture
    def mock_orchestrator_service(self):
        """Create a mock OrchestratorService with all dependencies mocked."""
        with patch.multiple(
            'orchestrator.service',
            QueueClient=MagicMock(),
            HttpClient=MagicMock(),
            DynamoDBSaver=MagicMock(),
            S3Client=MagicMock(),
            GraphBuilder=MagicMock(),
            CommandParser=MagicMock()
        ):
            # Mock environment variable
            with patch.dict(os.environ, {'STATE_TABLE_NAME': 'test-table'}):
                service = OrchestratorService()
                
                # Mock all the dependencies
                service.state_saver = MagicMock()
                service.s3_client = MagicMock()
                service.sqs_client = MagicMock()
                service.http_client = MagicMock()
                service.graph_cache = {}
                
                return service

    def test_case_a_no_checkpoint_found_new_workflow(self, mock_orchestrator_service):
        """
        Test Case A: No checkpoint found - should start new workflow.
        """
        # Arrange
        instance_id = "test-case-a-123"
        command_message = {
            "workflowInstanceId": instance_id,
            "workflowDefinitionURI": "s3://bucket/workflow.yaml",
            "command": {
                "type": "EVENT",
                "payload": {"test_data": "initial_value"}
            }
        }
        
        # Mock: No checkpoint found
        mock_orchestrator_service.state_saver.get.return_value = None
        
        # Mock: Graph compilation and execution
        mock_graph = MagicMock()
        mock_orchestrator_service._get_or_compile_graph = MagicMock(return_value=mock_graph)
        
        # Mock: CommandParser validation
        with patch('orchestrator.service.CommandParser.is_valid_command', return_value=True):
            # Act
            mock_orchestrator_service.process_command(command_message)
        
        # Assert
        # Verify checkpoint was checked
        mock_orchestrator_service.state_saver.get.assert_called_once_with(
            {"configurable": {"thread_id": instance_id}}
        )
        
        # Verify graph was invoked (new workflow started)
        mock_graph.invoke.assert_called_once()
        
        # Verify workflow definition was NOT fetched (because no checkpoint existed)
        mock_orchestrator_service.s3_client.get_workflow_definition.assert_not_called()
        
        logger.info("✓ Case A: New workflow started successfully")

    def test_case_b1_checkpoint_at_start_node_allow_update(self, mock_orchestrator_service):
        """
        Test Case B1: Checkpoint found but still at start node - should allow update.
        """
        # Arrange
        instance_id = "test-case-b1-123"
        command_message = {
            "workflowInstanceId": instance_id,
            "workflowDefinitionURI": "s3://bucket/workflow.yaml",
            "command": {
                "type": "EVENT",
                "payload": {"test_data": "updated_value"}
            }
        }
        
        # Mock: Checkpoint found at start node
        mock_checkpoint = {
            "channel_values": {"__next__": "Start_Node"}
        }
        mock_orchestrator_service.state_saver.get.return_value = mock_checkpoint
        
        # Mock: Workflow definition
        mock_workflow_def = {"entry_point": "Start_Node"}
        mock_orchestrator_service.s3_client.get_workflow_definition.return_value = mock_workflow_def
        
        # Mock: Graph compilation and execution
        mock_graph = MagicMock()
        mock_orchestrator_service._get_or_compile_graph = MagicMock(return_value=mock_graph)
        
        # Mock: CommandParser validation
        with patch('orchestrator.service.CommandParser.is_valid_command', return_value=True):
            # Act
            mock_orchestrator_service.process_command(command_message)
        
        # Assert
        # Verify checkpoint was checked
        mock_orchestrator_service.state_saver.get.assert_called_once_with(
            {"configurable": {"thread_id": instance_id}}
        )
        
        # Verify workflow definition was fetched to check entry point
        mock_orchestrator_service.s3_client.get_workflow_definition.assert_called_once_with(
            "s3://bucket/workflow.yaml"
        )
        
        # Verify graph was updated and invoked (update allowed)
        mock_graph.update_state.assert_called_once()
        mock_graph.invoke.assert_called_once()
        
        logger.info("✓ Case B1: Update allowed for workflow at start node")

    def test_case_b2_checkpoint_progressed_ignore_duplicate(self, mock_orchestrator_service):
        """
        Test Case B2: Checkpoint found and progressed past start - should ignore duplicate.
        """
        # Arrange
        instance_id = "test-case-b2-123"
        command_message = {
            "workflowInstanceId": instance_id,
            "workflowDefinitionURI": "s3://bucket/workflow.yaml",
            "command": {
                "type": "EVENT",
                "payload": {"test_data": "duplicate_value"}
            }
        }
        
        # Mock: Checkpoint found at progressed node
        mock_checkpoint = {
            "channel_values": {"__next__": "Some_Other_Node"}
        }
        mock_orchestrator_service.state_saver.get.return_value = mock_checkpoint
        
        # Mock: Workflow definition
        mock_workflow_def = {"entry_point": "Start_Node"}
        mock_orchestrator_service.s3_client.get_workflow_definition.return_value = mock_workflow_def
        
        # Mock: Graph compilation (should not be needed)
        mock_graph = MagicMock()
        mock_orchestrator_service._get_or_compile_graph = MagicMock(return_value=mock_graph)
        
        # Mock: CommandParser validation
        with patch('orchestrator.service.CommandParser.is_valid_command', return_value=True):
            # Act
            mock_orchestrator_service.process_command(command_message)
        
        # Assert
        # Verify checkpoint was checked
        mock_orchestrator_service.state_saver.get.assert_called_once_with(
            {"configurable": {"thread_id": instance_id}}
        )
        
        # Verify workflow definition was fetched to check entry point
        mock_orchestrator_service.s3_client.get_workflow_definition.assert_called_once()
        
        # Verify graph was NOT invoked (duplicate ignored)
        mock_graph.invoke.assert_not_called()
        mock_graph.update_state.assert_not_called()
        
        logger.info("✓ Case B2: Duplicate command ignored for progressed workflow")

    def test_case_c_non_event_commands_bypass_idempotency(self, mock_orchestrator_service):
        """
        Test Case C: Non-EVENT commands - should bypass idempotency check.
        """
        # Test ASYNC_RESP command
        instance_id = "test-case-c-async-123"
        async_resp_command = {
            "workflowInstanceId": instance_id,
            "workflowDefinitionURI": "s3://bucket/workflow.yaml",
            "command": {
                "type": "ASYNC_RESP",
                "payload": {"response": "test"}
            }
        }
        
        # Mock: Graph compilation and execution
        mock_graph = MagicMock()
        mock_orchestrator_service._get_or_compile_graph = MagicMock(return_value=mock_graph)
        
        # Mock: CommandParser validation
        with patch('orchestrator.service.CommandParser.is_valid_command', return_value=True):
            # Act
            mock_orchestrator_service.process_command(async_resp_command)
        
        # Assert
        # Verify checkpoint was NOT checked (idempotency bypassed)
        mock_orchestrator_service.state_saver.get.assert_not_called()
        
        # Test EXECUTE_SCHEDULED_TASK command
        mock_orchestrator_service.state_saver.reset_mock()
        
        instance_id = "test-case-c-scheduled-123"
        scheduled_command = {
            "workflowInstanceId": instance_id,
            "workflowDefinitionURI": "s3://bucket/workflow.yaml",
            "command": {
                "type": "EXECUTE_SCHEDULED_TASK",
                "payload": {"state_update": {"task": "complete"}}
            }
        }
        
        # Act
        mock_orchestrator_service.process_command(scheduled_command)
        
        # Assert
        # Verify checkpoint was NOT checked (idempotency bypassed)
        mock_orchestrator_service.state_saver.get.assert_not_called()
        
        logger.info("✓ Case C: Non-EVENT commands bypassed idempotency check")

    def test_entry_point_missing_error_handling(self, mock_orchestrator_service):
        """
        Test that missing entry_point in workflow definition is handled gracefully.
        """
        # Arrange
        instance_id = "test-missing-entry-point-123"
        command_message = {
            "workflowInstanceId": instance_id,
            "workflowDefinitionURI": "s3://bucket/workflow.yaml",
            "command": {
                "type": "EVENT",
                "payload": {"test_data": "test"}
            }
        }
        
        # Mock: Checkpoint found
        mock_checkpoint = {
            "channel_values": {"__next__": "Some_Node"}
        }
        mock_orchestrator_service.state_saver.get.return_value = mock_checkpoint
        
        # Mock: Workflow definition WITHOUT entry_point
        mock_workflow_def = {"workflow_name": "Test Workflow"}  # Missing entry_point
        mock_orchestrator_service.s3_client.get_workflow_definition.return_value = mock_workflow_def
        
        # Mock: Graph compilation (should not be reached)
        mock_graph = MagicMock()
        mock_orchestrator_service._get_or_compile_graph = MagicMock(return_value=mock_graph)
        
        # Mock: CommandParser validation
        with patch('orchestrator.service.CommandParser.is_valid_command', return_value=True):
            # Act
            mock_orchestrator_service.process_command(command_message)
        
        # Assert
        # Verify checkpoint was checked
        mock_orchestrator_service.state_saver.get.assert_called_once()
        
        # Verify workflow definition was fetched
        mock_orchestrator_service.s3_client.get_workflow_definition.assert_called_once()
        
        # Verify graph was NOT invoked due to missing entry_point
        mock_graph.invoke.assert_not_called()
        mock_graph.update_state.assert_not_called()
        
        logger.info("✓ Missing entry_point handled gracefully")

    def test_checkpoint_config_structure(self, mock_orchestrator_service):
        """
        Test that the checkpoint configuration uses the correct structure.
        """
        # Arrange
        instance_id = "test-config-structure-123"
        command_message = {
            "workflowInstanceId": instance_id,
            "workflowDefinitionURI": "s3://bucket/workflow.yaml",
            "command": {
                "type": "EVENT",
                "payload": {"test_data": "test"}
            }
        }
        
        # Mock: No checkpoint found
        mock_orchestrator_service.state_saver.get.return_value = None
        
        # Mock: Graph compilation
        mock_graph = MagicMock()
        mock_orchestrator_service._get_or_compile_graph = MagicMock(return_value=mock_graph)
        
        # Mock: CommandParser validation
        with patch('orchestrator.service.CommandParser.is_valid_command', return_value=True):
            # Act
            mock_orchestrator_service.process_command(command_message)
        
        # Assert
        # Verify checkpoint was checked with correct config structure
        expected_config = {"configurable": {"thread_id": instance_id}}
        mock_orchestrator_service.state_saver.get.assert_called_once_with(expected_config)
        
        logger.info("✓ Checkpoint configuration structure is correct")

    @pytest.mark.parametrize("next_node_val", [None, "Start_Node", ["Start_Node"], ("Start_Node",)])
    def test_start_node_comparison_logic(self, mock_orchestrator_service, next_node_val):
        """
        Test the start node comparison logic handles different formats correctly (None, str, list, tuple).
        """
        # Arrange
        instance_id = f"test-format-{type(next_node_val).__name__}-123"
        command_message = {
            "workflowInstanceId": instance_id,
            "workflowDefinitionURI": "s3://bucket/workflow.yaml",
            "command": { "type": "EVENT", "payload": {"test_data": "test"} }
        }
        
        # Mock: Checkpoint with different formats for __next__
        mock_checkpoint = {"channel_values": {"__next__": next_node_val}}
        mock_orchestrator_service.state_saver.get.return_value = mock_checkpoint
        
        # Mock: Workflow definition
        mock_workflow_def = {"entry_point": "Start_Node"}
        mock_orchestrator_service.s3_client.get_workflow_definition.return_value = mock_workflow_def
        
        # Mock: Graph compilation
        mock_graph = MagicMock()
        mock_orchestrator_service._get_or_compile_graph = MagicMock(return_value=mock_graph)
        
        # Mock: CommandParser validation
        with patch('orchestrator.service.CommandParser.is_valid_command', return_value=True):
            # Act
            mock_orchestrator_service.process_command(command_message)
        
        # Assert
        # Should allow update since it's at start node in all cases
        mock_graph.update_state.assert_called_once()
        mock_graph.invoke.assert_called_once()
        
        logger.info(f"✓ Start node comparison logic correctly handled format: {type(next_node_val).__name__}") 