import logging
import sys
import os
from pythonjsonlogger import jsonlogger

def setup_logging():
    """
    Configures the root logger for structured JSON logging.
    This setup is compatible with OpenTelemetry, which will automatically
    inject trace and span IDs into the logs when enabled.
    """
    logger = logging.getLogger()
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logger.setLevel(log_level)

    # Remove existing handlers to avoid duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    log_handler = logging.StreamHandler(sys.stdout)

    # These fields are expected in the final JSON log.
    # The python-json-logger library will automatically format the log record
    # and include any extra fields passed to the logger.
    # OTel fields (trace_id, span_id) are added automatically by its instrumentor.
    # Define the format for the JSON logs
    # These fields will be automatically populated from the LogRecord.
    # The format string specifies the fields that will be in the JSON output.
    # Standard LogRecord attributes are used, and OTEL attributes are added as extras.
    # Use standard LogRecord attributes in the format string.
    # The 'rename_fields' dict will map them to the desired JSON field names.
    # OTel fields are added automatically by the instrumentor and will be picked up.
    format_str = '%(asctime)s %(levelname)s %(name)s %(message)s %(filename)s %(lineno)d'
    formatter = jsonlogger.JsonFormatter(
        format_str,
        rename_fields={
            'asctime': 'timestamp',
            'levelname': 'level',
            'otelTraceID': 'trace_id',
            'otelSpanID': 'span_id',
            'otelServiceName': 'service_name'
        }
    )

    log_handler.setFormatter(formatter)
    logger.addHandler(log_handler)

    if logger.level <= logging.INFO:
        logger.info(f"Structured JSON logging configured with level {log_level}.")
