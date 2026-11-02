# Observability for this service (Area E): traces, metrics and logs, wired once at start.
#
# Traces (OpenTelemetry -> Jaeger): one span per HTTP request, continuing the caller's trace from
#   its traceparent header; one per RabbitMQ message consumed or published, carrying the trace in
#   the message headers; one per SQL statement. Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set.
# Metrics (Prometheus, GET /metrics): http_request_duration_seconds{service, method, route, status},
#   the same histogram the monolith and the other service expose.
# Logs: every line is one JSON object with the service name and, inside a traced request or
#   message, trace_id: the correlation id shared by every service's logs and by Jaeger.
import json
import logging
import os
import time

from opentelemetry import trace
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

SERVICE = os.environ.get("OTEL_SERVICE_NAME", "service")

REQUESTS = Histogram(
    "http_request_duration_seconds",
    "HTTP requests by route and status, and how long they took",
    ["service", "method", "route", "status"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
)
UNMEASURED = {"/health", "/metrics"}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        msg = record.getMessage()
        try:
            fields = json.loads(msg)
            if not isinstance(fields, dict):
                fields = {"msg": msg}
        except ValueError:
            fields = {"msg": msg}
        line = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname.lower(),
                "service": SERVICE, "logger": record.name}
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            line["trace_id"] = format(span.trace_id, "032x")
        line.update(fields)
        return json.dumps(line)


def setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def setup_tracing(app):
    """Must run before the database pool opens and before the RabbitMQ threads start."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.pika import PikaInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/metrics")
    PikaInstrumentor().instrument()
    PsycopgInstrumentor().instrument()


def setup_metrics(app):
    @app.middleware("http")
    async def measure(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", None) or "unmatched"
            if path not in UNMEASURED:
                REQUESTS.labels(SERVICE, request.method, path, str(status)).observe(time.perf_counter() - started)

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
