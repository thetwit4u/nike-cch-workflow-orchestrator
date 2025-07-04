from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from graphql import build_schema, graphql_sync
from graphql.execution import ExecutionResult
from starlette.graphql import GraphQLApp
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
import json
import logging
from typing import Any, Dict, Optional
from datetime import datetime
import os

# Import our workflow schema
try:
    from .workflow_schema import workflow_schema, workflow_schema_with_subscriptions
except ImportError:
    # Fallback if imports fail
    workflow_schema = None
    workflow_schema_with_subscriptions = None

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Workflow Orchestrator GraphQL API",
    description="GraphQL API for managing and visualizing workflow executions",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# GraphQL endpoint
if workflow_schema:
    graphql_app = GraphQLApp(schema=workflow_schema)
    app.mount("/graphql", graphql_app)

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint for the API."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }

# GraphQL introspection endpoint
@app.get("/graphql/schema")
async def get_graphql_schema():
    """Get the GraphQL schema SDL."""
    if not workflow_schema:
        raise HTTPException(status_code=503, detail="GraphQL schema not available")
    
    from graphql import print_schema
    return {"schema": print_schema(workflow_schema)}

# Custom GraphQL endpoint with better error handling
@app.post("/graphql/execute")
async def execute_graphql(request: Request):
    """Execute GraphQL queries with custom error handling."""
    try:
        body = await request.json()
        query = body.get("query")
        variables = body.get("variables")
        operation_name = body.get("operationName")
        
        if not query:
            raise HTTPException(status_code=400, detail="Query is required")
        
        if not workflow_schema:
            raise HTTPException(status_code=503, detail="GraphQL schema not available")
        
        # Execute the GraphQL query
        result = graphql_sync(
            schema=workflow_schema,
            source=query,
            variable_values=variables,
            operation_name=operation_name
        )
        
        # Format response
        response_data = {"data": result.data}
        if result.errors:
            response_data["errors"] = [str(error) for error in result.errors]
        
        return response_data
        
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.error(f"GraphQL execution error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

# Workflow management endpoints
@app.get("/workflows")
async def list_workflows(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """List workflow instances with optional filtering."""
    # This would typically query your database
    workflows = []
    for i in range(limit):
        workflows.append({
            "workflowInstanceId": f"workflow-{i + offset}",
            "status": status or "COMPLETED",
            "currentNode": "End_Workflow" if status == "COMPLETED" else "Create_Filing_Packs",
            "createdAt": datetime.now().isoformat()
        })
    
    return {
        "workflows": workflows,
        "total": 1000,  # Mock total
        "limit": limit,
        "offset": offset
    }

@app.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str):
    """Get a specific workflow instance."""
    # This would typically query your database
    return {
        "workflowInstanceId": workflow_id,
        "status": "RUNNING",
        "currentNode": "Create_Filing_Packs",
        "createdAt": datetime.now().isoformat(),
        "executionContext": {
            "deliverySetId": "test-delivery-set-123",
            "deliverySetURI": "s3://test-bucket/delivery-set.json"
        }
    }

@app.post("/workflows/start")
async def start_workflow(workflow_data: Dict[str, Any]):
    """Start a new workflow instance."""
    try:
        # Validate required fields
        required_fields = ["workflowDefinitionUri", "deliverySet"]
        for field in required_fields:
            if field not in workflow_data:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Missing required field: {field}"
                )
        
        # Generate workflow instance ID
        import uuid
        workflow_instance_id = str(uuid.uuid4())
        
        # Here you would typically:
        # 1. Send message to SQS queue
        # 2. Save workflow instance to database
        # 3. Return the created instance
        
        return {
            "workflowInstanceId": workflow_instance_id,
            "correlationId": workflow_data.get("correlationId", workflow_instance_id),
            "status": "PENDING",
            "createdAt": datetime.now().isoformat(),
            "message": f"Workflow {workflow_instance_id} started successfully"
        }
        
    except Exception as e:
        logger.error(f"Failed to start workflow: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to start workflow")

@app.post("/workflows/{workflow_id}/cancel")
async def cancel_workflow(workflow_id: str):
    """Cancel a running workflow."""
    try:
        # Here you would typically:
        # 1. Update workflow status to CANCELLED
        # 2. Send cancellation message to workflow orchestrator
        # 3. Clean up any resources
        
        return {
            "workflowInstanceId": workflow_id,
            "status": "CANCELLED",
            "message": f"Workflow {workflow_id} cancelled successfully"
        }
        
    except Exception as e:
        logger.error(f"Failed to cancel workflow: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to cancel workflow")

# Metrics endpoint
@app.get("/metrics")
async def get_metrics(
    workflow_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
):
    """Get workflow execution metrics."""
    # This would typically query your database
    return {
        "totalExecutions": 167,
        "successfulExecutions": 150,
        "failedExecutions": 10,
        "averageExecutionTimeSeconds": 45,
        "executionsByStatus": {
            "COMPLETED": 150,
            "FAILED": 10,
            "RUNNING": 5,
            "PENDING": 2
        }
    }

# WebSocket endpoint for real-time updates (optional)
@app.websocket("/ws/workflows/{workflow_id}")
async def workflow_websocket(websocket, workflow_id: str):
    """WebSocket endpoint for real-time workflow updates."""
    await websocket.accept()
    
    try:
        while True:
            # Send periodic updates
            await websocket.send_json({
                "workflowInstanceId": workflow_id,
                "status": "RUNNING",
                "currentNode": "Create_Filing_Packs",
                "timestamp": datetime.now().isoformat()
            })
            await asyncio.sleep(5)  # Send update every 5 seconds
            
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
    finally:
        await websocket.close()

# Startup event
@app.on_event("startup")
async def startup_event():
    """Startup event handler."""
    logger.info("Workflow GraphQL API starting up...")
    
    # Initialize connections, load configurations, etc.
    # For example, connect to database, initialize AWS clients, etc.
    
    logger.info("Workflow GraphQL API started successfully")

# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown event handler."""
    logger.info("Workflow GraphQL API shutting down...")
    
    # Clean up resources
    # Close database connections, clean up temporary files, etc.
    
    logger.info("Workflow GraphQL API shut down successfully")

# Error handlers
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Handle 404 errors."""
    return JSONResponse(
        status_code=404,
        content={"error": "Not found", "detail": "The requested resource was not found"}
    )

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    """Handle 500 errors."""
    logger.error(f"Internal server error: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": "An unexpected error occurred"}
    )

if __name__ == "__main__":
    import uvicorn
    
    # Get configuration from environment
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    
    # Run the server
    uvicorn.run(
        "api_server:app",
        host=host,
        port=port,
        reload=debug,
        log_level="info"
    )