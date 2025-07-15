import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import logging
import asyncio

from orchestrator.service import OrchestratorService
from utils.logger import setup_logging
from utils.command_parser import CommandParser

from opentelemetry import trace
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


# Configure logging at the entry point
setup_logging()

# Get a logger for this specific module
logger = logging.getLogger(__name__)

# Set up OpenTelemetry
provider = TracerProvider()
processor = BatchSpanProcessor(OTLPSpanExporter())
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)


LoggingInstrumentor().instrument(set_logging_format=True)
BotocoreInstrumentor().instrument(tracer_provider=provider)
RequestsInstrumentor().instrument(tracer_provider=provider)


# Initialize service as a singleton
orchestrator_service = OrchestratorService.get_instance()

def handler(event, context):
    """
    Main Lambda entry point.
    Processes SQS records by passing them to the OrchestratorService.
    """
    # The OrchestratorService's process_command is synchronous,
    # so we don't need an async main loop here anymore.
    logger.info(f"Received event: {json.dumps(event)}")

    for record in event['Records']:
        aws_trace_header = record["attributes"]["AWSTraceHeader"]
        tracer = trace.get_tracer("com.nike.custom-opentelemetry-instrumentation")
        span_context = None

        if aws_trace_header:
            logger.info(f"Received AWS trace header: {aws_trace_header}")
            traceparent = convert_aws_trace_header_to_parent_trace(aws_trace_header)
            logger.info(f"Converted AWS trace header to traceparent: {traceparent}")
            carrier = {"traceparent": traceparent}
            span_context = TraceContextTextMapPropagator().extract(carrier)

        with tracer.start_as_current_span(
            "lambda_handler",
            context=span_context
        ):
            try:
                message_body = json.loads(record['body'])
                orchestrator_service.process_command(message_body)
                
            except Exception:
                logger.exception(f"Error processing SQS record: {record.get('messageId', 'N/A')}")
                # For now, we'll just log and continue to the next record.
                # A DLQ should be configured on the SQS queue for production.
                continue
    provider.force_flush()      
    return {
        'statusCode': 200,
        'body': json.dumps('Successfully processed records.')
    }


def convert_aws_trace_header_to_parent_trace(aws_trace_header: str) -> str:
    parts = aws_trace_header.split(";")
    root, parent, sampled = "", "", ""
    for part in parts:
        if part.startswith("Root="):
            root = part[5:]
        elif part.startswith("Parent="):
            parent = part[7:]
        elif part.startswith("Sampled="):
            sampled = part[8:]

    root_parts = root.split("-")
    trace_id = root_parts[1] + root_parts[2] if len(root_parts) > 2 else ""

    span_id = parent

    flags = "00"
    if sampled == "1":
        flags = "01"
    return f"00-{trace_id}-{span_id}-{flags}"