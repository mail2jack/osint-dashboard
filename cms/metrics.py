import uuid
import time
import logging
from collections import defaultdict
from threading import Lock

logger = logging.getLogger(__name__)

_lock = Lock()
_request_counts = defaultdict(int)
_status_counts = defaultdict(int)
_method_counts = defaultdict(int)
_route_counts = defaultdict(int)
_duration_buckets = defaultdict(int)
_active_requests = 0

BUCKET_MS = [5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]


def record_request(method: str, path: str, status: int, duration_ms: float) -> None:
    with _lock:
        _request_counts["total"] += 1
        _method_counts[method] += 1
        _status_counts[status // 100 * 100] += 1
        _route_counts[f"{method} {path}"] += 1
        for bucket in BUCKET_MS:
            if duration_ms <= bucket:
                _duration_buckets[bucket] += 1
                break
        else:
            _duration_buckets[float("inf")] += 1


def inc_active_requests() -> None:
    global _active_requests
    with _lock:
        _active_requests += 1


def dec_active_requests() -> None:
    global _active_requests
    with _lock:
        _active_requests = max(0, _active_requests - 1)


def generate_metrics() -> str:
    with _lock:
        total = _request_counts.get("total", 0)
        active = _active_requests
        method_counts = dict(_method_counts)
        status_counts = dict(_status_counts)
        route_counts = dict(_route_counts)
        duration_buckets = dict(_duration_buckets)

    lines = [
        "# HELP http_requests_total Total HTTP requests",
        "# TYPE http_requests_total counter",
        f"http_requests_total {total}",
        "",
        "# HELP http_requests_active Currently active requests",
        "# TYPE http_requests_active gauge",
        f"http_requests_active {active}",
        "",
        "# HELP http_request_duration_ms Request duration histogram in ms",
        "# TYPE http_request_duration_ms histogram",
    ]
    for bucket in BUCKET_MS:
        count = duration_buckets.get(bucket, 0)
        lines.append(f'http_request_duration_ms_bucket{{le="{bucket}"}} {count}')
    lines.append(
        f'http_request_duration_ms_bucket{{le="+Inf"}} {duration_buckets.get(float("inf"), 0)}'
    )
    lines.append(f"http_request_duration_ms_count {total}")

    for method, count in sorted(method_counts.items()):
        lines.append("")
        lines.append("# HELP http_requests_by_method_total Requests by HTTP method")
        lines.append("# TYPE http_requests_by_method_total counter")
        lines.append(f'http_requests_by_method_total{{method="{method}"}} {count}')

    for code, count in sorted(status_counts.items()):
        lines.append("")
        lines.append(
            "# HELP http_requests_by_status_total Requests by HTTP status class"
        )
        lines.append("# TYPE http_requests_by_status_total counter")
        lines.append(f'http_requests_by_status_total{{status="{code}"}} {count}')

    if route_counts:
        lines.append("")
        lines.append("# HELP http_requests_by_route_total Requests by route")
        lines.append("# TYPE http_requests_by_route_total counter")
        for route, count in sorted(route_counts.items()):
            lines.append(f'http_requests_by_route_total{{route="{route}"}} {count}')

    lines.append("")
    return "\n".join(lines)


def register_metrics_route(app) -> None:
    from flask import Response

    @app.route("/metrics")
    def metrics():
        return Response(
            generate_metrics(),
            mimetype="text/plain; version=0.0.4",
        )

    @app.before_request
    def _start_timer():
        from flask import request, g
        from flask_login import current_user

        g._request_start = time.time()
        g.request_id = str(uuid.uuid4())[:8]
        g.endpoint = request.endpoint or request.path
        try:
            g.user_id = (
                current_user.get_id()
                if current_user and current_user.is_authenticated
                else "-"
            )
        except Exception:
            g.user_id = "-"
        inc_active_requests()

    @app.after_request
    def _log_request(response):
        from flask import request, g

        start = getattr(g, "_request_start", None)
        if start:
            duration_ms = (time.time() - start) * 1000
            path = request.path
            record_request(
                method=request.method,
                path=path,
                status=response.status_code,
                duration_ms=duration_ms,
            )
            performance_log = logging.getLogger("performance")
            performance_log.info(
                "%s %s → %d (%.0fms) [%s]",
                request.method,
                path,
                response.status_code,
                duration_ms,
                g.get("request_id", "-"),
                extra={
                    "method": request.method,
                    "path": path,
                    "status": response.status_code,
                    "duration_ms": round(duration_ms, 1),
                    "request_id": g.get("request_id", "-"),
                },
            )
        dec_active_requests()
        return response
