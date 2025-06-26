import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class DataLoader:
    """
    Loads test data from JSON files for BDD scenarios.
    """

    def __init__(self, data_dir: str = "tests/test_data"):
        """
        Initializes the loader.

        Args:
            data_dir: The relative path to the directory containing test data files.
        """
        self.data_dir = Path(data_dir).resolve()
        if not self.data_dir.is_dir():
            raise FileNotFoundError(f"Test data directory not found at: {self.data_dir}")

    def load_json(self, filename: str) -> dict:
        """
        Loads a specific JSON file from the test data directory.

        Args:
            filename: The name of the JSON file to load (e.g., 'test-data-happy-path.json').

        Returns:
            A dictionary containing the parsed JSON data.
        """
        file_path = self.data_dir / filename
        logger.info(f"Loading test data from {file_path}")
        
        if not file_path.exists():
            logger.error(f"Test data file not found: {file_path}")
            raise FileNotFoundError(f"Test data file not found: {file_path}")

        with open(file_path, 'r') as f:
            try:
                data = json.load(f)
                logger.info(f"Successfully loaded test data from {filename}")
                return data
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding JSON from file {file_path}: {e}")
                raise

    def get_file_path(self, filename: str) -> str:
        """
        Gets the full path to a test data file.

        Args:
            filename: The name of the file.

        Returns:
            The absolute path to the file as a string.
        """
        file_path = self.data_dir / filename
        if not file_path.exists():
            raise FileNotFoundError(f"Test data file not found: {file_path}")
        return str(file_path)
