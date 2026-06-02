import logging

perf_logger = logging.getLogger("performance")
req_logger = logging.getLogger("requests")
logger = logging.getLogger(__name__)


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


__all__ = ["perf_logger", "req_logger", "logger", "log_performance", "log_request"]
