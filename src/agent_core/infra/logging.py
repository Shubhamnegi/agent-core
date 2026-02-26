import logging
from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)

    if not root.handlers:
        stream_handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s request_id=%(request_id)s %(name)s %(message)s"
        )
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    for handler in root.handlers:
        has_filter = any(isinstance(log_filter, RequestIdFilter) for log_filter in handler.filters)
        if not has_filter:
            handler.addFilter(RequestIdFilter())
