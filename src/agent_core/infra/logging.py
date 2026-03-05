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


class PrettyConsoleFormatter(logging.Formatter):
    _RESET = "\033[0m"
    _DIM = "\033[2m"
    _BOLD = "\033[1m"
    _LEVEL_COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }

    def __init__(self, use_color: bool = True) -> None:
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        timestamp = self.formatTime(record, self.datefmt)
        level = record.levelname
        request_id = getattr(record, "request_id", "-")
        logger_name = record.name
        event = record.message

        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _BUILTIN_ATTRS:
                continue
            extras[key] = value

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            extras["exception"] = record.exc_text

        parts = [
            self._c(timestamp, self._DIM),
            self._c(f"{level:8}", self._LEVEL_COLORS.get(level, ""), bold=True),
            self._c(logger_name, "\033[94m"),
            self._c(f"rid={request_id}", self._DIM),
            self._c(event, "\033[96m"),
        ]
        line = " | ".join(parts)

        if extras:
            extras_text = json.dumps(extras, default=str, ensure_ascii=False)
            line = f"{line} {self._c(extras_text, '\033[90m')}"
        return line

    def _c(self, value: str, color: str, bold: bool = False) -> str:
        if not self.use_color or not color:
            return value
        weight = self._BOLD if bold else ""
        return f"{weight}{color}{value}{self._RESET}"


def configure_logging(
    level: str = "INFO",
    log_format: str = "pretty",
    color: bool = True,
) -> None:
    root = logging.getLogger()
    root.setLevel(level)

    formatter: logging.Formatter
    if log_format.lower() == "json":
        formatter = StructuredFormatter()
    else:
        formatter = PrettyConsoleFormatter(use_color=color)

    if not root.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    for handler in root.handlers:
        handler.setFormatter(formatter)
        has_filter = any(
            isinstance(log_filter, RequestIdFilter)
            for log_filter in handler.filters
        )
        if not has_filter:
            handler.addFilter(RequestIdFilter())
