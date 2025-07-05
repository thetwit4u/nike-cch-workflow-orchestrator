import logging
import uuid
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from dataclasses import dataclass

from orchestrator.state import WorkflowState
from clients.queue_client import QueueClient

logger = logging.getLogger(__name__)

# Initialize clients
queue_client = QueueClient()

# Define interrupt function for pausing workflows
def interrupt(message: str = "Workflow paused") -> WorkflowState:
    """Placeholder interrupt function - would use LangGraph's interrupt in real implementation"""
    # In a real implementation, this would use LangGraph's interrupt mechanism
    # For now, we'll return a special state that indicates interruption
    return {
        "workflow_definition_uri": "",
        "context": {"interrupt_message": message},
        "command": {},
        "data": {},
        "map_results": [],
        "branch_checkpoints": {},
        "current_operation": {},
        "is_error": False,
        "error_details": None
    }

@dataclass
class IssueCreationRequest:
    workflow_instance_id: str
    node_name: str
    error_details: Dict[str, Any]
    severity: str = "MEDIUM"
    category: str = "WORKFLOW_ERROR"
    assignee: Optional[str] = None

@dataclass
class WorkflowIssue:
    issue_id: str
    workflow_instance_id: str
    node_name: str
    error_type: str
    error_code: str
    error_message: str
    severity: str
    status: str
    assignee: Optional[str] = None
    external_issue_id: Optional[str] = None
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None

class IssueService:
    """Service for managing workflow issues"""
    
    def __init__(self):
        # In a real implementation, this would connect to a database
        self.issues = {}  # Mock in-memory storage
    
    async def create_issue(self, request: IssueCreationRequest) -> str:
        """Creates a new issue and returns the issue ID"""
        issue_id = str(uuid.uuid4())
        
        # Determine assignee
        assignee = self._determine_assignee(request)
        
        # Create issue record
        issue = WorkflowIssue(
            issue_id=issue_id,
            workflow_instance_id=request.workflow_instance_id,
            node_name=request.node_name,
            error_type=request.category,
            error_code=request.error_details.get("error_code", "UNKNOWN"),
            error_message=request.error_details.get("error", ""),
            severity=request.severity,
            status="CREATED",
            assignee=assignee,
            created_at=datetime.utcnow()
        )
        
        self.issues[issue_id] = issue
        
        logger.info(f"Created issue {issue_id} for workflow {request.workflow_instance_id}")
        
        # In a real implementation, this would:
        # - Store in database
        # - Create external tickets (JIRA, ServiceNow)
        # - Send notifications
        
        return issue_id
    
    def get_issue(self, issue_id: str) -> Optional[WorkflowIssue]:
        """Get issue by ID"""
        return self.issues.get(issue_id)
    
    async def resolve_issue(self, issue_id: str, status: str, notes: str, resolved_by: str, resolution_type: str = "MANUAL"):
        """Mark an issue as resolved"""
        issue = self.issues.get(issue_id)
        if not issue:
            raise ValueError(f"Issue {issue_id} not found")
        
        issue.status = status
        issue.resolution_notes = notes
        issue.resolved_at = datetime.utcnow()
        
        logger.info(f"Resolved issue {issue_id} with status {status}")
        
        # In a real implementation, this would:
        # - Update database
        # - Update external tickets
        # - Send notifications
    
    def _determine_assignee(self, request: IssueCreationRequest) -> str:
        """Determine assignee based on error details and rules"""
        error_code = request.error_details.get("error_code", "")
        
        # Simple assignee rules - in real implementation this would be configurable
        if error_code.startswith("S3-"):
            return "data-ops-team"
        elif error_code.startswith("CUSTOMS-"):
            return "customs-team"
        elif error_code.startswith("HTTP-"):
            return "integration-team"
        else:
            return "workflow-ops-team"

async def handle_create_issue(state: WorkflowState, node_config: dict, node_name: str) -> WorkflowState:
    """
    Handles 'create_issue' nodes by creating a trackable issue from workflow errors.
    """
    try:
        logger.info(f"Executing 'create_issue' node '{node_name}'")
        
        issue_config = node_config.get("issue_config", {})
        
        # Extract error details from state
        error_details = state.get("error_details", {})
        if not error_details:
            raise ValueError("No error details found in state for issue creation")
        
        # Create issue request
        request = IssueCreationRequest(
            workflow_instance_id=state["context"]["workflowInstanceId"],
            node_name=error_details.get("node", node_name),
            error_details=error_details,
            severity=issue_config.get("severity", "MEDIUM"),
            category=issue_config.get("category", "WORKFLOW_ERROR")
        )
        
        # Create the issue
        issue_service = IssueService()
        issue_id = await issue_service.create_issue(request)
        
        # Store issue ID in state for later reference
        state["data"]["current_issue_id"] = issue_id
        state["data"]["issue_created_at"] = datetime.utcnow().isoformat()
        
        logger.info(f"Successfully created issue {issue_id} for workflow error")
        
    except Exception as e:
        logger.error(f"Error in 'create_issue' node '{node_name}': {e}")
        state["is_error"] = True
        state["error_details"] = {"error": str(e), "node": node_name}
    
    return state

def handle_wait_for_resolution(state: WorkflowState, node_config: dict, node_name: str) -> WorkflowState:
    """
    Waits for external issue resolution before proceeding.
    """
    try:
        logger.info(f"Executing 'wait_for_resolution' node '{node_name}'")
        
        resolution_config = node_config.get("resolution_config", {})
        issue_id_key = resolution_config.get("issue_id_key", "current_issue_id")
        
        issue_id = state["data"].get(issue_id_key)
        if not issue_id:
            raise ValueError(f"Issue ID not found in state key '{issue_id_key}'")
        
        # Check current issue status
        issue_service = IssueService()
        issue = issue_service.get_issue(issue_id)
        
        if not issue:
            raise ValueError(f"Issue {issue_id} not found")
        
        if issue.status in ["RESOLVED", "CLOSED"]:
            logger.info(f"Issue {issue_id} is resolved (status: {issue.status}). Proceeding to resolution handler.")
            state["data"]["resolution_status"] = issue.status
            state["data"]["resolution_notes"] = issue.resolution_notes
            state["data"]["resolved_at"] = issue.resolved_at.isoformat() if issue.resolved_at else None
            return state
        
        # Check for timeout
        timeout_hours = resolution_config.get("timeout_hours", 48)
        issue_age = datetime.utcnow() - (issue.created_at or datetime.utcnow())
        if issue_age > timedelta(hours=timeout_hours):
            logger.warning(f"Issue {issue_id} has exceeded timeout of {timeout_hours} hours")
            state["data"]["resolution_timeout"] = True
            state["data"]["timeout_reason"] = f"Issue not resolved within {timeout_hours} hours"
            return state
        
        # Issue not yet resolved - interrupt workflow
        logger.info(f"Issue {issue_id} still open (status: {issue.status}). Workflow will pause.")
        return interrupt(f"Waiting for issue {issue_id} to be resolved")
        
    except Exception as e:
        logger.error(f"Error in 'wait_for_resolution' node '{node_name}': {e}")
        state["is_error"] = True
        state["error_details"] = {"error": str(e), "node": node_name}
    
    return state

def handle_reset_error_state(state: WorkflowState, node_config: dict, node_name: str) -> WorkflowState:
    """
    Resets error state and prepares workflow for resumption.
    """
    try:
        logger.info(f"Executing 'reset_error_state' node '{node_name}'")
        
        reset_config = node_config.get("reset_config", {})
        
        # Clear error state
        if reset_config.get("clear_error_flags", True):
            state["is_error"] = False
            state["error_details"] = None
            logger.info("Cleared error flags from workflow state")
        
        # Log resumption
        if reset_config.get("log_resumption", True):
            workflow_id = state["context"]["workflowInstanceId"]
            logger.info(f"Workflow {workflow_id} resuming after error resolution")
        
        # Set resumption metadata
        state["data"]["resumed_at"] = datetime.utcnow().isoformat()
        state["data"]["resumption_reason"] = "external_issue_resolved"
        state["data"]["error_state_reset"] = True
        
        # Optionally restore from checkpoint
        if reset_config.get("restore_from_checkpoint", False):
            # In a real implementation, this would restore from a saved checkpoint
            logger.info("Checkpoint restoration would be performed here")
        
        # Notify completion if configured
        if reset_config.get("notify_completion", True):
            # In a real implementation, send notification that workflow resumed
            logger.info("Notification sent: Workflow resumed after issue resolution")
        
        logger.info(f"Successfully reset error state for workflow resumption")
        
    except Exception as e:
        logger.error(f"Error in 'reset_error_state' node '{node_name}': {e}")
        state["is_error"] = True
        state["error_details"] = {"error": str(e), "node": node_name}
    
    return state

async def handle_enhanced_log_error(state: WorkflowState, node_config: dict, node_name: str) -> WorkflowState:
    """
    Enhanced error logging node that can optionally create issues.
    """
    try:
        error_config = node_config.get("error_config", {})
        
        # Get error details - handle None case
        error_details = state.get("error_details") or {}
        error_code = node_config.get("default_error_code", "UNKNOWN_ERROR")
        default_message = node_config.get("default_message", "An unspecified error occurred.")
        message_key = node_config.get("message_from_context_key")
        
        # Prioritize the dynamic message from the state if available
        dynamic_message = error_details.get(message_key) if message_key and error_details else None
        message = dynamic_message or error_details.get("error") or default_message
        
        # Determine severity
        severity_mapping = error_config.get("severity_mapping", {})
        severity = severity_mapping.get(error_code, severity_mapping.get("default", "MEDIUM"))
        
        # Enhanced log payload
        log_payload = {
            "error_code": error_code,
            "message": message,
            "severity": severity,
            "workflow_instance_id": state["context"].get("workflowInstanceId"),
            "correlation_id": state["context"].get("correlationId"),
            "failed_node": error_details.get("node") if error_details else None,
            "context_data": error_details.get("context", {}) if error_details else {},
            "timestamp": datetime.utcnow().isoformat()
        }
        
        logger.error(f"Enhanced error log: {json.dumps(log_payload, indent=2)}")
        
        # Create issue if configured
        if error_config.get("create_issue", False):
            logger.info("Creating issue from enhanced error log")
            # Add issue creation logic here
            enhanced_error_details = {
                "error_code": error_code,
                "severity": severity,
                "context": log_payload
            }
            if error_details:
                enhanced_error_details.update(error_details)
            
            request = IssueCreationRequest(
                workflow_instance_id=state["context"]["workflowInstanceId"],
                node_name=error_details.get("node", node_name) if error_details else node_name,
                error_details=enhanced_error_details,
                severity=severity,
                category="ENHANCED_LOG_ERROR"
            )
            
            issue_service = IssueService()
            issue_id = await issue_service.create_issue(request)
            state["data"]["auto_created_issue_id"] = issue_id
        
        # Send notifications if configured
        if error_config.get("notify_teams", False):
            logger.info("Team notifications would be sent here")
        
        # Clear the error state unless configured otherwise
        if not error_config.get("preserve_error_state", False):
            state["is_error"] = False
            state["error_details"] = None
        
    except Exception as e:
        logger.critical(f"CRITICAL: Failed to execute enhanced_log_error node '{node_name}'. Error: {e}")
        # We still clear the state to prevent potential loops
        state["is_error"] = False
        state["error_details"] = None
    
    return state

# Trigger for external issue resolution
async def trigger_workflow_resumption(workflow_instance_id: str, issue_id: str):
    """
    Triggers workflow resumption by sending a resume command.
    """
    command_message = {
        "workflowInstanceId": workflow_instance_id,
        "command": {
            "type": "ISSUE_RESOLVED",
            "id": str(uuid.uuid4()),
            "source": "IssueResolutionService",
            "timestamp": datetime.utcnow().isoformat(),
            "payload": {
                "issue_id": issue_id,
                "resolution_trigger": True
            }
        }
    }
    
    logger.info(f"Triggering workflow resumption for {workflow_instance_id} after issue {issue_id} resolution")
    
    # Send to orchestrator command queue
    # In a real implementation, this would use the actual queue configuration
    try:
        await queue_client.send_message(
            queue_url="workflow-command-queue",  # This would come from environment
            message_body=json.dumps(command_message)
        )
        logger.info(f"Successfully sent resumption command for workflow {workflow_instance_id}")
    except Exception as e:
        logger.error(f"Failed to send resumption command: {e}")
        raise