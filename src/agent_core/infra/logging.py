import json
import logging
from contextvars import ContextVar
from typing import Any

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_BUILTIN_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "taskName", "thread", "threadName",
        "request_id",
    }
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()  # type: ignore[attr-defined]
        return True


class StructuredFormatter(logging.Formatter):
    """Render every log line as a single JSON object.

    Includes all ``extra`` fields passed via ``logger.info(..., extra={})``
    so that agent / sub-agent request & response data is always visible.
    """

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        log_entry: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "request_id": getattr(record, "request_id", "-"),
            "logger": record.name,
            "event": record.message,
        }
        # Collect extra fields
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _BUILTIN_ATTRS:
                continue
            log_entry[key] = value
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            log_entry["exception"] = record.exc_text
        return json.dumps(log_entry, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)

    if not root.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(StructuredFormatter())
        root.addHandler(stream_handler)

    for handler in root.handlers:
        # Upgrade existing handlers to structured format
        if not isinstance(handler.formatter, StructuredFormatter):
            handler.setFormatter(StructuredFormatter())
        has_filter = any(
            isinstance(log_filter, RequestIdFilter)
            for log_filter in handler.filters
        )
        if not has_filter:
            handler.addFilter(RequestIdFilter())
