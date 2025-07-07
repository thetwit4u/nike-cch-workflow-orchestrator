from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import uuid
import json
import os
import logging
from datetime import datetime
import asyncio

from orchestrator.nodes.enhanced_error_nodes import IssueService, trigger_workflow_resumption, WorkflowIssue
from clients.queue_client import QueueClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Workflow Issue Resolution API",
    description="API for managing workflow issues and triggering resumption",
    version="1.0.0"
)

# Security
security = HTTPBearer()
queue_client = QueueClient()

# Pydantic Models
class IssueResolutionRequest(BaseModel):
    external_issue_id: Optional[str] = Field(None, description="External system issue ID")
    resolution_status: str = Field(..., description="RESOLVED, CLOSED, CANCELLED")
    resolution_notes: str = Field(..., description="Details about the resolution")
    resolved_by: str = Field(..., description="Who resolved the issue")
    resolution_type: str = Field(default="EXTERNAL", description="AUTO, MANUAL, EXTERNAL")
    additional_context: Optional[Dict[str, Any]] = Field(None, description="Additional context data")

class IssueAssignmentRequest(BaseModel):
    assignee: str = Field(..., description="New assignee for the issue")
    assignment_reason: Optional[str] = Field(None, description="Reason for assignment")

class IssueEscalationRequest(BaseModel):
    escalation_reason: str = Field(..., description="Reason for escalation")
    new_assignee: Optional[str] = Field(None, description="New assignee after escalation")
    escalation_level: str = Field(default="MANAGER", description="MANAGER, DIRECTOR, VP")

class IssueQueryParams(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None
    assignee: Optional[str] = None
    workflow_instance_id: Optional[str] = None
    limit: int = Field(default=50, le=100)
    offset: int = Field(default=0, ge=0)

class IssueResponse(BaseModel):
    issue_id: str
    workflow_instance_id: str
    node_name: str
    error_type: str
    error_code: str
    error_message: str
    severity: str
    status: str
    assignee: Optional[str]
    external_issue_id: Optional[str]
    created_at: Optional[datetime]
    resolved_at: Optional[datetime]
    resolution_notes: Optional[str]

class ResolutionResult(BaseModel):
    success: bool
    message: str
    issue_id: str
    workflow_resumption_triggered: bool
    resumption_command_id: Optional[str] = None

class AssignmentResult(BaseModel):
    success: bool
    message: str
    issue_id: str
    previous_assignee: Optional[str]
    new_assignee: str

class EscalationResult(BaseModel):
    success: bool
    message: str
    issue_id: str
    escalation_level: str
    escalated_to: Optional[str]

class IssueMetrics(BaseModel):
    total_issues: int
    open_issues: int
    resolved_issues: int
    average_resolution_time_hours: float
    issues_by_status: Dict[str, int]
    issues_by_severity: Dict[str, int]
    issues_by_assignee: Dict[str, int]

# Dependency for authentication
async def get_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Validate API key from Authorization header"""
    expected_key = os.getenv("ISSUE_RESOLUTION_API_KEY", "dev-key-12345")
    if credentials.credentials != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    return credentials.credentials

# Initialize issue service
issue_service = IssueService()

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.get("/api/v1/issues", response_model=List[IssueResponse])
async def list_issues(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    assignee: Optional[str] = None,
    workflow_instance_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    api_key: str = Depends(get_api_key)
):
    """
    List issues with optional filtering
    """
    try:
        # In a real implementation, this would query the database
        # For now, we'll return mock data from the in-memory store
        issues = []
        for issue in issue_service.issues.values():
            # Apply filters
            if status and issue.status != status:
                continue
            if severity and issue.severity != severity:
                continue
            if assignee and issue.assignee != assignee:
                continue
            if workflow_instance_id and issue.workflow_instance_id != workflow_instance_id:
                continue
            
            issues.append(IssueResponse(
                issue_id=issue.issue_id,
                workflow_instance_id=issue.workflow_instance_id,
                node_name=issue.node_name,
                error_type=issue.error_type,
                error_code=issue.error_code,
                error_message=issue.error_message,
                severity=issue.severity,
                status=issue.status,
                assignee=issue.assignee,
                external_issue_id=issue.external_issue_id,
                created_at=issue.created_at,
                resolved_at=issue.resolved_at,
                resolution_notes=issue.resolution_notes
            ))
        
        # Apply pagination
        paginated_issues = issues[offset:offset + limit]
        
        logger.info(f"Retrieved {len(paginated_issues)} issues (total: {len(issues)})")
        return paginated_issues
        
    except Exception as e:
        logger.error(f"Error listing issues: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/v1/issues/{issue_id}", response_model=IssueResponse)
async def get_issue(issue_id: str, api_key: str = Depends(get_api_key)):
    """
    Get specific issue by ID
    """
    try:
        issue = issue_service.get_issue(issue_id)
        if not issue:
            raise HTTPException(status_code=404, detail="Issue not found")
        
        return IssueResponse(
            issue_id=issue.issue_id,
            workflow_instance_id=issue.workflow_instance_id,
            node_name=issue.node_name,
            error_type=issue.error_type,
            error_code=issue.error_code,
            error_message=issue.error_message,
            severity=issue.severity,
            status=issue.status,
            assignee=issue.assignee,
            external_issue_id=issue.external_issue_id,
            created_at=issue.created_at,
            resolved_at=issue.resolved_at,
            resolution_notes=issue.resolution_notes
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting issue {issue_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/v1/issues/{issue_id}/resolve", response_model=ResolutionResult)
async def resolve_issue(
    issue_id: str,
    resolution: IssueResolutionRequest,
    api_key: str = Depends(get_api_key)
):
    """
    Mark an issue as resolved and trigger workflow resumption
    """
    try:
        # Validate issue exists
        issue = issue_service.get_issue(issue_id)
        if not issue:
            raise HTTPException(status_code=404, detail="Issue not found")
        
        # Validate resolution status
        valid_statuses = ["RESOLVED", "CLOSED", "CANCELLED"]
        if resolution.resolution_status not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid resolution status. Must be one of: {valid_statuses}"
            )
        
        # Update issue status
        await issue_service.resolve_issue(
            issue_id=issue_id,
            status=resolution.resolution_status,
            notes=resolution.resolution_notes,
            resolved_by=resolution.resolved_by,
            resolution_type=resolution.resolution_type
        )
        
        workflow_resumption_triggered = False
        resumption_command_id = None
        
        # Trigger workflow resumption if issue is resolved/closed
        if resolution.resolution_status in ["RESOLVED", "CLOSED"]:
            try:
                resumption_command_id = str(uuid.uuid4())
                await trigger_workflow_resumption(issue.workflow_instance_id, issue_id)
                workflow_resumption_triggered = True
                logger.info(f"Triggered workflow resumption for {issue.workflow_instance_id}")
            except Exception as e:
                logger.error(f"Failed to trigger workflow resumption: {e}")
                # Don't fail the resolution if resumption fails
        
        result = ResolutionResult(
            success=True,
            message=f"Issue {issue_id} resolved successfully",
            issue_id=issue_id,
            workflow_resumption_triggered=workflow_resumption_triggered,
            resumption_command_id=resumption_command_id
        )
        
        logger.info(f"Successfully resolved issue {issue_id} with status {resolution.resolution_status}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resolving issue {issue_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/v1/issues/{issue_id}/assign", response_model=AssignmentResult)
async def assign_issue(
    issue_id: str,
    assignment: IssueAssignmentRequest,
    api_key: str = Depends(get_api_key)
):
    """
    Assign an issue to a new assignee
    """
    try:
        issue = issue_service.get_issue(issue_id)
        if not issue:
            raise HTTPException(status_code=404, detail="Issue not found")
        
        previous_assignee = issue.assignee
        
        # Update assignee
        issue.assignee = assignment.assignee
        
        # In a real implementation, this would:
        # - Update database
        # - Send notifications
        # - Record history
        
        logger.info(f"Assigned issue {issue_id} from {previous_assignee} to {assignment.assignee}")
        
        return AssignmentResult(
            success=True,
            message=f"Issue {issue_id} assigned to {assignment.assignee}",
            issue_id=issue_id,
            previous_assignee=previous_assignee,
            new_assignee=assignment.assignee
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error assigning issue {issue_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/v1/issues/{issue_id}/escalate", response_model=EscalationResult)
async def escalate_issue(
    issue_id: str,
    escalation: IssueEscalationRequest,
    api_key: str = Depends(get_api_key)
):
    """
    Escalate an issue to a higher level
    """
    try:
        issue = issue_service.get_issue(issue_id)
        if not issue:
            raise HTTPException(status_code=404, detail="Issue not found")
        
        # Determine escalation target
        escalated_to = escalation.new_assignee
        if not escalated_to:
            # Default escalation mapping
            escalation_mapping = {
                "MANAGER": "team-manager",
                "DIRECTOR": "engineering-director", 
                "VP": "vp-engineering"
            }
            escalated_to = escalation_mapping.get(escalation.escalation_level, "escalation-team")
        
        # Update issue
        issue.assignee = escalated_to
        issue.severity = "CRITICAL"  # Escalated issues become critical
        
        # In a real implementation, this would:
        # - Update database
        # - Create escalation record
        # - Send urgent notifications
        # - Record history
        
        logger.info(f"Escalated issue {issue_id} to {escalation.escalation_level}: {escalated_to}")
        
        return EscalationResult(
            success=True,
            message=f"Issue {issue_id} escalated to {escalation.escalation_level}",
            issue_id=issue_id,
            escalation_level=escalation.escalation_level,
            escalated_to=escalated_to
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error escalating issue {issue_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/v1/metrics", response_model=IssueMetrics)
async def get_issue_metrics(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    assignee: Optional[str] = None,
    api_key: str = Depends(get_api_key)
):
    """
    Get issue metrics and analytics
    """
    try:
        issues = list(issue_service.issues.values())
        
        # Apply date filters if provided
        if date_from:
            date_from_parsed = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            issues = [i for i in issues if i.created_at and i.created_at >= date_from_parsed]
        
        if date_to:
            date_to_parsed = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            issues = [i for i in issues if i.created_at and i.created_at <= date_to_parsed]
        
        if assignee:
            issues = [i for i in issues if i.assignee == assignee]
        
        # Calculate metrics
        total_issues = len(issues)
        open_issues = len([i for i in issues if i.status not in ["RESOLVED", "CLOSED"]])
        resolved_issues = len([i for i in issues if i.status in ["RESOLVED", "CLOSED"]])
        
        # Calculate average resolution time
        resolved_with_times = [
            i for i in issues 
            if i.status in ["RESOLVED", "CLOSED"] and i.created_at and i.resolved_at
        ]
        
        if resolved_with_times:
            total_resolution_time = sum([
                (i.resolved_at - i.created_at).total_seconds() / 3600  # hours
                for i in resolved_with_times
            ])
            average_resolution_time = total_resolution_time / len(resolved_with_times)
        else:
            average_resolution_time = 0.0
        
        # Group by status
        issues_by_status = {}
        for issue in issues:
            issues_by_status[issue.status] = issues_by_status.get(issue.status, 0) + 1
        
        # Group by severity
        issues_by_severity = {}
        for issue in issues:
            issues_by_severity[issue.severity] = issues_by_severity.get(issue.severity, 0) + 1
        
        # Group by assignee
        issues_by_assignee = {}
        for issue in issues:
            if issue.assignee:
                issues_by_assignee[issue.assignee] = issues_by_assignee.get(issue.assignee, 0) + 1
        
        return IssueMetrics(
            total_issues=total_issues,
            open_issues=open_issues,
            resolved_issues=resolved_issues,
            average_resolution_time_hours=average_resolution_time,
            issues_by_status=issues_by_status,
            issues_by_severity=issues_by_severity,
            issues_by_assignee=issues_by_assignee
        )
        
    except Exception as e:
        logger.error(f"Error getting issue metrics: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/v1/workflows/{workflow_instance_id}/resume")
async def manual_workflow_resume(
    workflow_instance_id: str,
    api_key: str = Depends(get_api_key)
):
    """
    Manually trigger workflow resumption (for emergency cases)
    """
    try:
        command_message = {
            "workflowInstanceId": workflow_instance_id,
            "command": {
                "type": "MANUAL_RESUME",
                "id": str(uuid.uuid4()),
                "source": "ManualResumptionAPI",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {
                    "manual_resumption": True,
                    "triggered_by": "api_user"
                }
            }
        }
        
        # Send to orchestrator command queue
        await queue_client.send_message(
            queue_url=os.environ.get("COMMAND_QUEUE_URL", "workflow-command-queue"),
            message_body=json.dumps(command_message)
        )
        
        logger.info(f"Manually triggered resumption for workflow {workflow_instance_id}")
        
        return {
            "success": True,
            "message": f"Manual resumption triggered for workflow {workflow_instance_id}",
            "command_id": command_message["command"]["id"]
        }
        
    except Exception as e:
        logger.error(f"Error manually resuming workflow {workflow_instance_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# Webhook endpoint for external systems
@app.post("/api/v1/webhooks/external-resolution")
async def external_resolution_webhook(
    payload: Dict[str, Any],
    api_key: str = Depends(get_api_key)
):
    """
    Webhook endpoint for external issue tracking systems (JIRA, ServiceNow, etc.)
    """
    try:
        # Extract issue information from payload
        # This would be customized based on the external system format
        external_issue_id = payload.get("issue_id") or payload.get("ticket_id")
        status = payload.get("status")
        resolution_notes = payload.get("resolution", {}).get("description", "")
        resolved_by = payload.get("assignee", {}).get("displayName", "external-system")
        
        if not external_issue_id or not status:
            raise HTTPException(
                status_code=400,
                detail="Missing required fields: issue_id and status"
            )
        
        # Find internal issue by external ID
        internal_issue = None
        for issue in issue_service.issues.values():
            if issue.external_issue_id == external_issue_id:
                internal_issue = issue
                break
        
        if not internal_issue:
            logger.warning(f"No internal issue found for external ID: {external_issue_id}")
            return {"success": False, "message": "Internal issue not found"}
        
        # Map external status to internal status
        status_mapping = {
            "Done": "RESOLVED",
            "Closed": "CLOSED", 
            "Cancelled": "CANCELLED",
            "Resolved": "RESOLVED"
        }
        
        internal_status = status_mapping.get(status, "RESOLVED")
        
        # Resolve the issue
        resolution_request = IssueResolutionRequest(
            external_issue_id=external_issue_id,
            resolution_status=internal_status,
            resolution_notes=resolution_notes,
            resolved_by=resolved_by,
            resolution_type="EXTERNAL"
        )
        
        result = await resolve_issue(internal_issue.issue_id, resolution_request, api_key)
        
        logger.info(f"Processed external resolution webhook for issue {internal_issue.issue_id}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing external resolution webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("API_PORT", "8000")),
        log_level="info"
    )