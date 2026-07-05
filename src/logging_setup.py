"""Structured JSON logging for oneFill.

Usage (once, before any other imports produce log output):
    from src.logging_setup import setup_logging
    setup_logging(level=logging.INFO, json_mode=True)
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Include structured extras passed via `extra=` kwarg or LoggerAdapter
        for key in ("intent_id", "leg_id", "phase", "venue"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info and record.exc_info[1] is not None:
            payload["exc"] = str(record.exc_info[1])
        return json.dumps(payload, default=str)


class StructuredLogger(logging.LoggerAdapter):
    """LoggerAdapter that merges context (intent_id, leg_id, phase, venue)
    into log records so the JSON formatter can pick them up.
    """

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        for key in ("intent_id", "leg_id", "phase", "venue"):
            val = getattr(self, key, None)
            if val is not None and key not in extra:
                extra[key] = val
        return msg, kwargs


def setup_logging(
    level: int = logging.INFO,
    json_mode: bool = False,
    logger_names: list[str] | None = None,
) -> None:
    """Configure the root logger (or named loggers) for oneFill.

    In *json_mode* every log line is a JSON object (suitable for file
    sinks and log aggregators).  Otherwise a human-readable format is
    used with level, logger name, and message.
    """
    handler = logging.StreamHandler(sys.stderr)
    if json_mode:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )

    for name in logger_names or [None]:  # None = root logger
        logger = logging.getLogger(name)
        logger.setLevel(level)
        # Replace any existing handlers
        logger.handlers = [handler]


def get_structured_logger(name: str, **ctx: str) -> StructuredLogger:
    """Return a logger that automatically attaches *ctx* fields to every record."""
    base = logging.getLogger(name)
    return StructuredLogger(base, ctx)
