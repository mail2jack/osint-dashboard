import logging
from urllib.parse import urlsplit, urlunsplit

perf_logger = logging.getLogger("performance")
req_logger = logging.getLogger("requests")
logger = logging.getLogger(__name__)


def safe_url(url: str) -> str:
    """URL zonder credentials — veilig om te loggen (password nooit mee)."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        netloc = parts.hostname or ""
        if parts.port is not None:
            netloc += f":{parts.port}"
        return urlunsplit(
            (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
        )
    except ValueError:
        return "<unparsable-url>"


def log_performance(operation, duration, details=None):
    msg = f"{operation}: {duration:.3f}s"
    if details:
        msg += f" - {details}"
    perf_logger.info(
        msg,
        extra={
            "extra_data": {
                "operation": operation,
                "duration": duration,
                "details": details,
            }
        },
    )


def log_request(tool, query, status, found_count=0, checked=0):
    req_logger.info(
        f"{tool.upper()} | {query} | {status} | found:{found_count} | checked:{checked}",
        extra={
            "extra_data": {
                "tool": tool,
                "query": query,
                "status": status,
                "found_count": found_count,
                "checked": checked,
            }
        },
    )


__all__ = [
    "perf_logger",
    "req_logger",
    "logger",
    "log_performance",
    "log_request",
    "safe_url",
]
