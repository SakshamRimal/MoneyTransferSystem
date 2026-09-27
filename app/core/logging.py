"""
Structured logging configuration.

Emits JSON log lines (production-friendly for log aggregators) and injects a
per-request correlation id (via contextvars) into every log record so that
all logs emitted while handling a single HTTP request can be grep'd/joined
together. Falls back to plain text formatting when `log_json=False`, which
is handy for local development readability.
"""

import logging
import sys
from contextvars import ContextVar
from typing import Any

from pythonjsonlogger import jsonlogger

from app.core.config import get_settings

# Holds the current request's correlation id. Set by the logging middleware
# at the start of each request and reset at the end.
correlation_id_ctx_var: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    """Injects the current correlation id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_ctx_var.get()
        return True


class _JsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record.setdefault("correlation_id", getattr(record, "correlation_id", "-"))


def configure_logging() -> None:
    """Configure the root logger. Call once at application startup."""
    settings = get_settings()
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level.upper())

    # Avoid duplicate handlers on reload/import.
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(CorrelationIdFilter())

    if settings.log_json:
        formatter: logging.Formatter = _JsonFormatter(
            "%(asctime)s %(level)s %(logger)s %(correlation_id)s %(message)s"
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(correlation_id)s | %(name)s | %(message)s"
        )

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Quiet down noisy third-party loggers unless we're debugging.
    for noisy in ("sqlalchemy.engine", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.INFO if settings.debug else logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper mirroring `logging.getLogger` for consistent imports."""
    return logging.getLogger(name)
