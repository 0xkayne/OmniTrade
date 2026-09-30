"""Observability — metrics, tracing, and telemetry for Omnitrade."""

from .metrics import MetricsEmitter, NoopMetrics

__all__ = ["MetricsEmitter", "NoopMetrics"]
