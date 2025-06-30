import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from jsonpath_ng.ext import parse as jsonpath_parse

from clients.s3_client import S3Client
from orchestrator.state import WorkflowState

logger = logging.getLogger(__name__)

def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    """Parses an S3 URI into bucket and key."""
    parsed_uri = urlparse(s3_uri)
    if parsed_uri.scheme != 's3':
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    
    bucket = parsed_uri.netloc
    key = parsed_uri.path.lstrip('/')
    return bucket, key

def _get_required_param(params: dict, key: str):
    """Gets a required parameter or raises a ValueError."""
    value = params.get(key)
    if value is None:
        raise ValueError(f"Missing required parameter: {key}")
    return value

def _resolve_state_key(state: dict, key: str):
    """Resolves a potentially nested key from the workflow state."""
    parts = key.split('.')
    value = state
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return None # Key not found
        value = value[part]
    return value

def handle_s3_read_jsonpath(state: WorkflowState, node_config: dict, node_name: str) -> dict:
    """
    Node handler for the 's3#read_jsonpath' library call.

    Reads a JSON file from S3, applies a JSONPath expression, and stores
    the result in the 'data' dictionary of the workflow state.
    """
    logger.info(f"Executing s3#read_jsonpath node '{node_name}'")
    try:
        params = node_config.get("parameters", {})
        
        uri_context_key = _get_required_param(params, "input_s3_uri_key")
        s3_uri = _resolve_state_key(state, uri_context_key)
        if not s3_uri:
            raise ValueError(f"S3 URI not found in state using key '{uri_context_key}'.")

        bucket, key = _parse_s3_uri(s3_uri)
        expression = _get_required_param(params, "jsonpath_expression")
        destination = node_config.get("output_key")
        if not destination:
            raise ValueError("library_call node for s3#read_jsonpath must have an 'output_key'.")

        s3_client = S3Client()
        json_content = s3_client.read_json(bucket, key)

        jsonpath_expression = jsonpath_parse(expression)
        match = jsonpath_expression.find(json_content)
        
        if not match:
            raise ValueError(f"JSONPath expression '{expression}' yielded no results.")
            
        result = match[0].value
        logger.info(f"JSONPath query result: {result}")

        # Construct a dictionary representing the patch to be applied to the 'data' state.
        # This ensures we don't overwrite other data fields.
        output_patch = {}
        current_level = output_patch
        keys = destination.split('.')
        for part in keys[:-1]:
            current_level = current_level.setdefault(part, {})
        current_level[keys[-1]] = result
        
        state["data"].update(output_patch)
        return state

    except Exception as e:
        logger.error(f"Error in 's3#read_jsonpath' node '{node_name}': {e}")
        return {"is_error": True, "error_details": {"error": str(e), "node": node_name}}


def handle_core_calculate_timedelta(state: WorkflowState, node_config: dict, node_name: str) -> dict:
    """
    Node handler for the 'core#calculate_timedelta' library call.
    Calculates a new datetime by applying a delta to a base time.
    """
    logger.info(f"Executing core#calculate_timedelta node '{node_name}'")
    try:
        params = node_config.get("parameters", {})

        date_context_key = _get_required_param(params, "date_context_key")
        base_time_str = _resolve_state_key(state, date_context_key)
        
        # Explicitly check for a valid base time string.
        if not base_time_str:
            raise ValueError(f"Base date not found or is empty in state at key '{date_context_key}'.")

        delta_config = _get_required_param(params, "timedelta")
        destination = node_config.get("output_key")
        if not destination:
            raise ValueError("library_call node for core#calculate_timedelta must have an 'output_key'.")

        base_time = datetime.fromisoformat(base_time_str)

        # Use timedelta constructor with kwargs from the config
        delta = timedelta(**delta_config)

        new_time = (base_time + delta).isoformat()
        logger.info(f"Calculated new time: {new_time}")

        # Construct a dictionary representing the patch to be applied to the 'data' state.
        output_patch = {}
        current_level = output_patch
        keys = destination.split('.')
        for part in keys[:-1]:
            current_level = current_level.setdefault(part, {})
        current_level[keys[-1]] = new_time

        state["data"].update(output_patch)
        return state

    except Exception as e:
        logger.error(f"Error in 'core#calculate_timedelta' node '{node_name}': {e}")
        return {"is_error": True, "error_details": {"error": str(e), "node": node_name}} 