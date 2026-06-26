"""OpenTelemetry instrumentation hooks.

Databricks Apps telemetry (Public Preview) captures system logs/usage and can
ingest custom OpenTelemetry spans/metrics into Unity Catalog
(``otel_logs`` / ``otel_spans`` / ``otel_metrics``). When telemetry is enabled
for the app, the platform sets ``OTEL_EXPORTER_OTLP_ENDPOINT`` and the spans we
emit here flow to UC automatically.

This module is intentionally defensive: if the OTel packages or endpoint are
absent (e.g. local dev), instrumentation is a no-op and the app still runs.
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


def setup_telemetry(app) -> None:
    if not settings.OTEL_ENABLED and not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        logger.info("OpenTelemetry disabled (set OTEL_ENABLED=true to instrument FastAPI).")
        return
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({
            "service.name": settings.PROJECT_NAME,
            "service.version": settings.APP_VERSION,
            "deployment.environment": settings.ENVIRONMENT,
        })
        provider = TracerProvider(resource=resource)
        if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT))
            )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        logger.info("OpenTelemetry FastAPI instrumentation enabled.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenTelemetry setup skipped: %s", exc)
