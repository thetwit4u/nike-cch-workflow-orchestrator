import os
import logging
from opentelemetry import trace
from opentelemetry.instrumentation.boto3sqs import Boto3SQSInstrumentor
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

logger = logging.getLogger(__name__)

def setup_telemetry():
    """
    Sets up OpenTelemetry tracing and instrumentation.

    Returns:
        tuple: A tuple containing the configured TracerProvider and Tracer.
               Returns (None, None) if OpenTelemetry is disabled.
    """
    if os.environ.get('DISABLE_OPENTELEMETRY', 'false').lower() == 'true':
        logger.info("OpenTelemetry is disabled.")
        return None, None

    logger.info("Setting up OpenTelemetry...")

    resource = Resource.create({SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "cch-workflow-orchestrator")})
    provider = TracerProvider(resource=resource)
    # Using OTLPSpanExporter for HTTP transport by default
    processor = BatchSpanProcessor(OTLPSpanExporter())
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    # Instrument libraries
    LoggingInstrumentor().instrument(set_logging_format=True)
    BotocoreInstrumentor().instrument(tracer_provider=provider)
    Boto3SQSInstrumentor().instrument(tracer_provider=provider)
    RequestsInstrumentor().instrument(tracer_provider=provider)

    tracer = trace.get_tracer("com.nike.cch.workflow-orchestrator")
    logger.info("OpenTelemetry setup complete.")

    return provider, tracer
