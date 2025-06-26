import requests
import logging

logger = logging.getLogger(__name__)

class MockServiceClient:
    """
    A client for configuring and interacting with the mock capability services.

    This client communicates with a control endpoint on the mock services to
    set up specific responses for test scenarios (e.g., success, failure, delays).
    """

    def __init__(self, base_url: str):
        """
        Initializes the client with the base URL of the mock service API.

        Args:
            base_url: The base endpoint of the mock services, retrieved from CDK outputs.
                      e.g., https://<api-id>.execute-api.<region>.amazonaws.com/prod/
        """
        if not base_url.endswith('/'):
            base_url += '/'
        self.base_url = base_url
        self.configure_endpoint = f"{self.base_url}control/configure"
        self.reset_endpoint = f"{self.base_url}control/reset"

    def configure_response(self, capability: str, response_type: str, response_data: dict = None):
        """
        Configures a mock capability to return a specific response.

        Args:
            capability: The name of the capability to configure (e.g., 'import#create_filingpacks').
            response_type: The type of response to return ('SUCCESS', 'FAILURE', 'TIMEOUT').
            response_data: The JSON payload to return in the response body.
        """
        payload = {
            "capability": capability,
            "response_type": response_type,
            "response_data": response_data or {}
        }
        logger.info(f"Configuring mock for '{capability}' at {self.configure_endpoint} with payload: {payload}")
        try:
            response = requests.post(self.configure_endpoint, json=payload)
            response.raise_for_status()  # Raises an HTTPError for bad responses (4xx or 5xx)
            logger.info(f"Successfully configured mock for '{capability}'. Response: {response.json()}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to configure mock for '{capability}': {e}")
            raise

    def reset_all(self):
        """
        Resets all mock configurations to their default states.
        """
        logger.info(f"Resetting all mock configurations via {self.reset_endpoint}")
        try:
            response = requests.post(self.reset_endpoint)
            response.raise_for_status()
            logger.info("Successfully reset all mock configurations.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to reset mock configurations: {e}")
            raise 