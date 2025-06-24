import logging
import sys

def setup_logging():
    """
    Configures the root logger for the application.
    This sets the log level and format for all log messages.
    """
    # The [WorkflowID] will be part of the message itself thanks to the adapter
    log_format = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        stream=sys.stdout,
        force=True
    )
    logging.info("Logging configured.")
