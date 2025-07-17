import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import logging

from orchestrator.service import OrchestratorService
from utils.logger import setup_logging
from utils.telemetry import setup_telemetry

from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.trace import Status, StatusCode

# Configure logging and telemetry at the entry point
setup_logging()
provider, tracer = setup_telemetry()

logger = logging.getLogger(__name__)

# Initialize service as a singleton
orchestrator_service = OrchestratorService.get_instance()

def handler(event, context):
    """
    Main Lambda entry point.
    Processes SQS records by passing them to the OrchestratorService.
    """
    logger.info("Received event", extra={"event": event})

    for record in event['Records']:
        message_id = record.get('messageId', 'N/A')
        log_extras = {
            'message_id': message_id,
            'event_source_arn': record.get('eventSourceARN'),
            'invocation_id': context.aws_request_id
        }

        if not tracer:
            try:
                logger.info("Processing SQS record without tracing.", extra=log_extras)
                message_body = json.loads(record['body'])
                orchestrator_service.process_command(message_body)
            except Exception as e:
                logger.exception("Error processing SQS record", extra=log_extras, exc_info=e)
            continue

        # With tracing enabled
        aws_trace_header = record.get("attributes", {}).get("AWSTraceHeader")
        span_context = None
        if aws_trace_header:
            traceparent = convert_aws_trace_header_to_parent_trace(aws_trace_header)
            carrier = {"traceparent": traceparent}
            span_context = TraceContextTextMapPropagator().extract(carrier)
            logger.info("Extracted trace context from SQS message", extra=log_extras)

        with tracer.start_as_current_span(
            "cch.workflow.process_record",
            context=span_context,
            kind=trace.SpanKind.CONSUMER
        ) as span:
            # Enrich span with semantic attributes
            span.set_attribute("faas.invocation_id", context.aws_request_id)
            span.set_attribute("messaging.system", "aws.sqs")
            if 'eventSourceARN' in record:
                span.set_attribute("messaging.source.name", record['eventSourceARN'])
            if 'messageId' in record:
                span.set_attribute("messaging.message.id", record['messageId'])

            try:
                logger.info("Processing SQS record with tracing.", extra=log_extras)
                message_body = json.loads(record['body'])
                span.add_event("Starting command processing", attributes={"body": record['body']})
                orchestrator_service.process_command(message_body)
                span.set_status(Status(StatusCode.OK, "Record processed successfully"))

            except Exception as e:
                logger.exception("Error processing SQS record", extra=log_extras, exc_info=e)
                # Record exception in the span
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Error processing record: {e}"))

    if provider:
        logger.info("Flushing OpenTelemetry provider.")
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