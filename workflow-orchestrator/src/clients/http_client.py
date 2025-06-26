import requests
import logging

logger = logging.getLogger(__name__)

class HttpClient:
    """
    A simple wrapper around the requests library for making HTTP calls.
    """

    def post(self, url: str, payload: dict, timeout: int = 30) -> dict:
        """
        Sends a POST request to the given URL.

        Args:
            url: The endpoint URL to send the request to.
            payload: The JSON payload to send in the request body.
            timeout: The request timeout in seconds.

        Returns:
            The JSON response from the service.
            
        Raises:
            requests.exceptions.RequestException: For connection errors or HTTP error statuses.
        """
        logger.info(f"Sending POST request to {url}")
        try:
            response = requests.post(url, json=payload, timeout=timeout)
            response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
            logger.info(f"Received successful response from {url}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP request to {url} failed: {e}")
            raise 