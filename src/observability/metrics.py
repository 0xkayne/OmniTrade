"""Lightweight metrics emitter Protocol + no-op default."""

from __future__ import annotations

from typing import Protocol


class MetricsEmitter(Protocol):
    """Callbacks for recording operational metrics.

    Concrete implementations could forward to Prometheus, Datadog,
    statsd, or any other backend.  The default *NoopMetrics* discards
    everything so no caller needs to guard for None.
    """

    def increment(self, name: str, value: float = 1, tags: dict[str, str] | None = None) -> None:
        """Record a counter increment (e.g. ``intent.submitted``)."""
        ...

    def histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a timing / distribution value in milliseconds."""
        ...


class NoopMetrics(MetricsEmitter):
    """Default / null-object metrics backend — silently discards everything."""

    def increment(self, name: str, value: float = 1, tags: dict[str, str] | None = None) -> None:
        pass

    def histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        pass
