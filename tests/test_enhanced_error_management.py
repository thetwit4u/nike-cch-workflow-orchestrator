import pytest
import asyncio
import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, AsyncMock
import sys
import os

# Add the src directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'workflow-orchestrator', 'src'))

from orchestrator.nodes.enhanced_error_nodes import (
    IssueService, 
    IssueCreationRequest, 
    WorkflowIssue,
    handle_create_issue,
    handle_wait_for_resolution,
    handle_reset_error_state,
    handle_enhanced_log_error,
    trigger_workflow_resumption
)

class TestEnhancedErrorManagement:
    """Test suite for enhanced error management functionality"""

    def setup_method(self):
        """Set up test fixtures"""
        self.issue_service = IssueService()
        self.mock_workflow_state = {
            "workflow_definition_uri": "s3://test-bucket/workflow.yaml",
            "context": {
                "workflowInstanceId": "test-workflow-123",
                "correlationId": "test-correlation-456"
            },
            "command": {},
            "data": {
                "dataURI": "s3://test-bucket/test-data.json",
                "deliverySetId": "DS-12345"
            },
            "map_results": [],
            "branch_checkpoints": {},
            "current_operation": {},
            "is_error": False,
            "error_details": None
        }

    # Issue Service Tests
    @pytest.mark.asyncio
    async def test_issue_service_create_issue(self):
        """Test basic issue creation functionality"""
        request = IssueCreationRequest(
            workflow_instance_id="test-workflow-123",
            node_name="test-node",
            error_details={
                "error": "S3 access denied",
                "error_code": "S3-403",
                "node": "test-node"
            },
            severity="HIGH",
            category="S3_ACCESS_ERROR"
        )
        
        issue_id = await self.issue_service.create_issue(request)
        
        assert issue_id is not None
        assert len(issue_id) == 36  # UUID length
        
        # Verify issue was stored
        issue = self.issue_service.get_issue(issue_id)
        assert issue is not None
        assert issue.workflow_instance_id == "test-workflow-123"
        assert issue.error_code == "S3-403"
        assert issue.severity == "HIGH"
        assert issue.status == "CREATED"
        assert issue.assignee == "data-ops-team"  # Based on S3-403 rule

    @pytest.mark.asyncio
    async def test_issue_service_resolve_issue(self):
        """Test issue resolution functionality"""
        # Create an issue first
        request = IssueCreationRequest(
            workflow_instance_id="test-workflow-123",
            node_name="test-node",
            error_details={"error": "Test error", "error_code": "TEST-001"},
            severity="MEDIUM"
        )
        
        issue_id = await self.issue_service.create_issue(request)
        
        # Resolve the issue
        await self.issue_service.resolve_issue(
            issue_id=issue_id,
            status="RESOLVED",
            notes="Issue fixed by restarting service",
            resolved_by="ops-team",
            resolution_type="MANUAL"
        )
        
        # Verify resolution
        issue = self.issue_service.get_issue(issue_id)
        assert issue.status == "RESOLVED"
        assert issue.resolution_notes == "Issue fixed by restarting service"
        assert issue.resolved_at is not None

    def test_assignee_determination_rules(self):
        """Test automatic assignee determination based on error codes"""
        # Test S3 error assignment
        request = IssueCreationRequest(
            workflow_instance_id="test-workflow-123",
            node_name="test-node",
            error_details={"error_code": "S3-001"},
            severity="MEDIUM"
        )
        assignee = self.issue_service._determine_assignee(request)
        assert assignee == "data-ops-team"
        
        # Test customs error assignment
        request.error_details = {"error_code": "CUSTOMS-002"}
        assignee = self.issue_service._determine_assignee(request)
        assert assignee == "customs-team"
        
        # Test HTTP error assignment
        request.error_details = {"error_code": "HTTP-500"}
        assignee = self.issue_service._determine_assignee(request)
        assert assignee == "integration-team"
        
        # Test default assignment
        request.error_details = {"error_code": "UNKNOWN-001"}
        assignee = self.issue_service._determine_assignee(request)
        assert assignee == "workflow-ops-team"

    # Create Issue Node Tests
    @pytest.mark.asyncio
    async def test_handle_create_issue_success(self):
        """Test successful issue creation node execution"""
        # Set up error state
        self.mock_workflow_state["is_error"] = True
        self.mock_workflow_state["error_details"] = {
            "error": "S3 bucket not accessible",
            "error_code": "S3-001",
            "node": "S3_Read_Node"
        }
        
        node_config = {
            "issue_config": {
                "severity": "HIGH",
                "category": "S3_ACCESS_ERROR"
            }
        }
        
        result_state = await handle_create_issue(
            self.mock_workflow_state, 
            node_config, 
            "Create_S3_Issue"
        )
        
        # Verify issue was created and stored in state
        assert "current_issue_id" in result_state["data"]
        assert "issue_created_at" in result_state["data"]
        
        issue_id = result_state["data"]["current_issue_id"]
        issue = self.issue_service.get_issue(issue_id)
        assert issue is not None
        assert issue.error_code == "S3-001"
        assert issue.severity == "HIGH"

    @pytest.mark.asyncio
    async def test_handle_create_issue_no_error_details(self):
        """Test create issue node when no error details are present"""
        node_config = {
            "issue_config": {
                "severity": "MEDIUM",
                "category": "GENERAL_ERROR"
            }
        }
        
        result_state = await handle_create_issue(
            self.mock_workflow_state,
            node_config,
            "Create_Issue_No_Error"
        )
        
        # Should set error state due to missing error details
        assert result_state["is_error"] == True
        assert "error_details" in result_state
        assert "No error details found" in result_state["error_details"]["error"]

    # Wait for Resolution Node Tests
    @pytest.mark.asyncio
    async def test_handle_wait_for_resolution_issue_resolved(self):
        """Test wait for resolution when issue is already resolved"""
        # Create and resolve an issue
        request = IssueCreationRequest(
            workflow_instance_id="test-workflow-123",
            node_name="test-node",
            error_details={"error": "Test error"},
            severity="MEDIUM"
        )
        
        issue_id = await self.issue_service.create_issue(request)
        await self.issue_service.resolve_issue(
            issue_id, "RESOLVED", "Fixed", "ops-team"
        )
        
        # Set up state with issue ID
        self.mock_workflow_state["data"]["current_issue_id"] = issue_id
        
        node_config = {
            "resolution_config": {
                "issue_id_key": "current_issue_id",
                "timeout_hours": 24
            }
        }
        
        result_state = await handle_wait_for_resolution(
            self.mock_workflow_state,
            node_config,
            "Wait_For_Resolution"
        )
        
        # Should proceed with resolution data
        assert result_state["data"]["resolution_status"] == "RESOLVED"
        assert result_state["data"]["resolution_notes"] == "Fixed"
        assert "resolved_at" in result_state["data"]

    @pytest.mark.asyncio
    async def test_handle_wait_for_resolution_issue_pending(self):
        """Test wait for resolution when issue is still pending"""
        # Create an unresolved issue
        request = IssueCreationRequest(
            workflow_instance_id="test-workflow-123",
            node_name="test-node",
            error_details={"error": "Test error"},
            severity="MEDIUM"
        )
        
        issue_id = await self.issue_service.create_issue(request)
        
        # Set up state with issue ID
        self.mock_workflow_state["data"]["current_issue_id"] = issue_id
        
        node_config = {
            "resolution_config": {
                "issue_id_key": "current_issue_id",
                "timeout_hours": 24
            }
        }
        
        result_state = await handle_wait_for_resolution(
            self.mock_workflow_state,
            node_config,
            "Wait_For_Resolution"
        )
        
        # Should return interrupt state
        assert "interrupt_message" in result_state["context"]
        assert "Waiting for issue" in result_state["context"]["interrupt_message"]

    @pytest.mark.asyncio
    async def test_handle_wait_for_resolution_timeout(self):
        """Test wait for resolution when issue has timed out"""
        # Create an issue with old timestamp
        request = IssueCreationRequest(
            workflow_instance_id="test-workflow-123",
            node_name="test-node",
            error_details={"error": "Test error"},
            severity="MEDIUM"
        )
        
        issue_id = await self.issue_service.create_issue(request)
        
        # Manually set old creation time
        issue = self.issue_service.get_issue(issue_id)
        issue.created_at = datetime.utcnow() - timedelta(hours=50)
        
        # Set up state with issue ID
        self.mock_workflow_state["data"]["current_issue_id"] = issue_id
        
        node_config = {
            "resolution_config": {
                "issue_id_key": "current_issue_id",
                "timeout_hours": 24
            }
        }
        
        result_state = await handle_wait_for_resolution(
            self.mock_workflow_state,
            node_config,
            "Wait_For_Resolution"
        )
        
        # Should indicate timeout
        assert result_state["data"]["resolution_timeout"] == True
        assert "timeout_reason" in result_state["data"]

    # Reset Error State Node Tests
    @pytest.mark.asyncio
    async def test_handle_reset_error_state_success(self):
        """Test successful error state reset"""
        # Set up error state
        self.mock_workflow_state["is_error"] = True
        self.mock_workflow_state["error_details"] = {
            "error": "Previous error",
            "node": "Previous_Node"
        }
        
        node_config = {
            "reset_config": {
                "clear_error_flags": True,
                "log_resumption": True,
                "notify_completion": True
            }
        }
        
        result_state = await handle_reset_error_state(
            self.mock_workflow_state,
            node_config,
            "Reset_Error_State"
        )
        
        # Verify error state is cleared
        assert result_state["is_error"] == False
        assert result_state["error_details"] is None
        
        # Verify resumption metadata
        assert "resumed_at" in result_state["data"]
        assert result_state["data"]["resumption_reason"] == "external_issue_resolved"
        assert result_state["data"]["error_state_reset"] == True

    @pytest.mark.asyncio
    async def test_handle_reset_error_state_preserve_error(self):
        """Test error state reset with error preservation"""
        # Set up error state
        self.mock_workflow_state["is_error"] = True
        self.mock_workflow_state["error_details"] = {
            "error": "Previous error",
            "node": "Previous_Node"
        }
        
        node_config = {
            "reset_config": {
                "clear_error_flags": False,  # Don't clear errors
                "log_resumption": True
            }
        }
        
        result_state = await handle_reset_error_state(
            self.mock_workflow_state,
            node_config,
            "Reset_Error_State"
        )
        
        # Verify error state is preserved
        assert result_state["is_error"] == True
        assert result_state["error_details"] is not None

    # Enhanced Log Error Node Tests
    @pytest.mark.asyncio
    async def test_handle_enhanced_log_error_with_issue_creation(self):
        """Test enhanced log error node with automatic issue creation"""
        # Set up error state
        self.mock_workflow_state["error_details"] = {
            "error": "Database connection failed",
            "error_code": "DB-001",
            "node": "Database_Read_Node"
        }
        
        node_config = {
            "error_config": {
                "create_issue": True,
                "severity_mapping": {
                    "DB-001": "HIGH",
                    "default": "MEDIUM"
                },
                "notify_teams": True
            },
            "default_error_code": "DB-001",
            "default_message": "Database error occurred"
        }
        
        result_state = await handle_enhanced_log_error(
            self.mock_workflow_state,
            node_config,
            "Enhanced_Log_Error"
        )
        
        # Verify issue was created automatically
        assert "auto_created_issue_id" in result_state["data"]
        
        issue_id = result_state["data"]["auto_created_issue_id"]
        issue = self.issue_service.get_issue(issue_id)
        assert issue is not None
        assert issue.severity == "HIGH"  # From severity mapping
        assert issue.error_code == "DB-001"

    @pytest.mark.asyncio
    async def test_handle_enhanced_log_error_preserve_state(self):
        """Test enhanced log error with error state preservation"""
        # Set up error state
        self.mock_workflow_state["error_details"] = {
            "error": "Test error",
            "node": "Test_Node"
        }
        
        node_config = {
            "error_config": {
                "create_issue": False,
                "preserve_error_state": True  # Don't clear error state
            },
            "default_error_code": "TEST-001"
        }
        
        result_state = await handle_enhanced_log_error(
            self.mock_workflow_state,
            node_config,
            "Enhanced_Log_Error"
        )
        
        # Verify error state is preserved
        assert result_state["is_error"] == True
        assert result_state["error_details"] is not None

    # Workflow Resumption Tests
    @pytest.mark.asyncio
    @patch('orchestrator.nodes.enhanced_error_nodes.queue_client')
    async def test_trigger_workflow_resumption(self, mock_queue_client):
        """Test workflow resumption trigger"""
        mock_queue_client.send_message = AsyncMock()
        
        workflow_instance_id = "test-workflow-123"
        issue_id = "test-issue-456"
        
        await trigger_workflow_resumption(workflow_instance_id, issue_id)
        
        # Verify message was sent to queue
        mock_queue_client.send_message.assert_called_once()
        call_args = mock_queue_client.send_message.call_args
        
        # Verify message content
        message_body = json.loads(call_args[1]["message_body"])
        assert message_body["workflowInstanceId"] == workflow_instance_id
        assert message_body["command"]["type"] == "ISSUE_RESOLVED"
        assert message_body["command"]["payload"]["issue_id"] == issue_id

    # Integration Tests
    @pytest.mark.asyncio
    async def test_full_error_lifecycle(self):
        """Test complete error lifecycle: creation -> waiting -> resolution -> reset"""
        # Step 1: Create Issue
        self.mock_workflow_state["is_error"] = True
        self.mock_workflow_state["error_details"] = {
            "error": "S3 access error",
            "error_code": "S3-001",
            "node": "S3_Read_Node"
        }
        
        create_config = {
            "issue_config": {
                "severity": "HIGH",
                "category": "S3_ACCESS_ERROR"
            }
        }
        
        state_after_create = await handle_create_issue(
            self.mock_workflow_state,
            create_config,
            "Create_Issue"
        )
        
        issue_id = state_after_create["data"]["current_issue_id"]
        assert issue_id is not None
        
        # Step 2: Wait for Resolution (issue still pending)
        wait_config = {
            "resolution_config": {
                "issue_id_key": "current_issue_id",
                "timeout_hours": 24
            }
        }
        
        state_after_wait = await handle_wait_for_resolution(
            state_after_create,
            wait_config,
            "Wait_For_Resolution"
        )
        
        # Should interrupt (issue not resolved yet)
        assert "interrupt_message" in state_after_wait["context"]
        
        # Step 3: Resolve Issue Externally
        await self.issue_service.resolve_issue(
            issue_id, "RESOLVED", "S3 bucket permissions fixed", "ops-team"
        )
        
        # Step 4: Wait for Resolution Again (now resolved)
        state_after_resolution = await handle_wait_for_resolution(
            state_after_create,  # Use original state with issue ID
            wait_config,
            "Wait_For_Resolution"
        )
        
        # Should proceed with resolution data
        assert state_after_resolution["data"]["resolution_status"] == "RESOLVED"
        
        # Step 5: Reset Error State
        reset_config = {
            "reset_config": {
                "clear_error_flags": True,
                "log_resumption": True
            }
        }
        
        final_state = await handle_reset_error_state(
            state_after_resolution,
            reset_config,
            "Reset_Error_State"
        )
        
        # Verify final state
        assert final_state["is_error"] == False
        assert final_state["error_details"] is None
        assert final_state["data"]["resumed_at"] is not None

    @pytest.mark.asyncio
    async def test_multiple_issues_same_workflow(self):
        """Test handling multiple issues for the same workflow"""
        workflow_id = "test-workflow-multiple"
        
        # Create first issue
        request1 = IssueCreationRequest(
            workflow_instance_id=workflow_id,
            node_name="S3_Node",
            error_details={"error_code": "S3-001"},
            severity="HIGH"
        )
        issue_id1 = await self.issue_service.create_issue(request1)
        
        # Create second issue
        request2 = IssueCreationRequest(
            workflow_instance_id=workflow_id,
            node_name="Customs_Node",
            error_details={"error_code": "CUSTOMS-001"},
            severity="MEDIUM"
        )
        issue_id2 = await self.issue_service.create_issue(request2)
        
        # Verify both issues exist
        issue1 = self.issue_service.get_issue(issue_id1)
        issue2 = self.issue_service.get_issue(issue_id2)
        
        assert issue1.workflow_instance_id == workflow_id
        assert issue2.workflow_instance_id == workflow_id
        assert issue1.assignee == "data-ops-team"
        assert issue2.assignee == "customs-team"
        
        # Resolve first issue
        await self.issue_service.resolve_issue(
            issue_id1, "RESOLVED", "S3 fixed", "data-ops"
        )
        
        # Verify only first issue is resolved
        issue1_updated = self.issue_service.get_issue(issue_id1)
        issue2_updated = self.issue_service.get_issue(issue_id2)
        
        assert issue1_updated.status == "RESOLVED"
        assert issue2_updated.status == "CREATED"

    # Error Scenario Tests
    @pytest.mark.asyncio
    async def test_handle_wait_for_resolution_missing_issue_id(self):
        """Test wait for resolution when issue ID is missing from state"""
        node_config = {
            "resolution_config": {
                "issue_id_key": "missing_issue_id",
                "timeout_hours": 24
            }
        }
        
        result_state = await handle_wait_for_resolution(
            self.mock_workflow_state,
            node_config,
            "Wait_For_Resolution"
        )
        
        # Should set error state
        assert result_state["is_error"] == True
        assert "Issue ID not found" in result_state["error_details"]["error"]

    @pytest.mark.asyncio
    async def test_handle_wait_for_resolution_nonexistent_issue(self):
        """Test wait for resolution with non-existent issue ID"""
        # Set up state with non-existent issue ID
        self.mock_workflow_state["data"]["current_issue_id"] = "non-existent-issue"
        
        node_config = {
            "resolution_config": {
                "issue_id_key": "current_issue_id",
                "timeout_hours": 24
            }
        }
        
        result_state = await handle_wait_for_resolution(
            self.mock_workflow_state,
            node_config,
            "Wait_For_Resolution"
        )
        
        # Should set error state
        assert result_state["is_error"] == True
        assert "not found" in result_state["error_details"]["error"]

    def test_issue_service_get_nonexistent_issue(self):
        """Test getting a non-existent issue"""
        issue = self.issue_service.get_issue("non-existent-id")
        assert issue is None

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_issue(self):
        """Test resolving a non-existent issue"""
        with pytest.raises(ValueError, match="Issue .* not found"):
            await self.issue_service.resolve_issue(
                "non-existent-id", "RESOLVED", "Test", "user"
            )

# Test Runner
if __name__ == "__main__":
    # Run the tests
    pytest.main([__file__, "-v", "--tb=short"])