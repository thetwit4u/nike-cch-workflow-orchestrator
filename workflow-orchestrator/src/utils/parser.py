import yaml
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def parse_workflow_definition(yaml_content: str) -> Dict[str, Any]:
    """
    Parses a YAML string into a Python dictionary.

    :param yaml_content: The string content of the YAML file.
    :return: A dictionary representing the parsed YAML.
    """
    try:
        definition = yaml.safe_load(yaml_content)
        logger.info("Successfully parsed workflow definition YAML.")
        return definition
    except yaml.YAMLError as e:
        logger.error(f"Error parsing workflow definition YAML: {e}")
        raise ValueError("Invalid YAML format in workflow definition.") from e 