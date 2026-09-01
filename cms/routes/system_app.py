from .response import api_error

"""
App-level system routes: health, version, config, docs, error handlers.
Registered directly on the Flask app (not the CMS blueprint).
"""

import json
import logging
import signal

import flask
from flask import Flask, request, jsonify, render_template, redirect, url_for, g
from flask_login import login_required

logger = logging.getLogger(__name__)


def register_system_routes(app: Flask) -> None:
    """Register system-level routes on the Flask app."""

    @app.route("/")
    def index() -> flask.Response:
        return redirect(url_for("cms.dashboard"))

    @app.route("/api/version", methods=["GET"])
    @login_required
    def get_version() -> flask.Response:
        from version import get_version_info

        return jsonify(get_version_info())

    @app.route("/api/changelog", methods=["GET"])
    @login_required
    def get_changelog() -> flask.Response:
        import os

        _root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        cl_path = os.path.join(_root, "CHANGELOG.md")
        logger.info("CHANGELOG path: %s exists: %s", cl_path, os.path.exists(cl_path))
        if not os.path.exists(cl_path):
            return jsonify({"html": "<p>No changelog available.</p>"})
        with open(cl_path) as f:
            raw = f.read()
        logger.info("CHANGELOG read %d bytes", len(raw))
        lines = raw.split("\n")
        html_parts = []
        for line in lines:
            if line.startswith("### "):
                html_parts.append(f"<p><strong>{line[4:]}</strong></p>")
            elif line.startswith("## "):
                html_parts.append(f"<h3>{line[3:]}</h3>")
            elif line.startswith("- "):
                html_parts.append(f"<li>{line[2:]}</li>")
            elif line.strip() == "":
                html_parts.append("<br>")
            else:
                html_parts.append(f"<p>{line}</p>")
        result = "".join(html_parts)
        logger.info("CHANGELOG html length: %d", len(result))
        return jsonify({"html": result})

    @app.route("/api/config", methods=["GET"])
    @login_required
    def get_config_api() -> flask.Response:
        from cms.models import Setting

        return jsonify(
            {
                "overheid_key_configured": bool(Setting.get("overheid_api_key")),
                "twochat_configured": bool(
                    Setting.get("twochat_api_key")
                    and Setting.get("twochat_whatsapp_number")
                ),
                "hibp_key_configured": bool(Setting.get("hibp_api_key")),
                "ai_available": _check_ollama_available(),
            }
        )

    @app.route("/api/docs")
    @login_required
    def api_docs() -> str:
        return render_template("cms/api_docs.html")

    @app.route("/api/openapi.json")
    @login_required
    def openapi_spec() -> flask.Response:
        spec = _build_openapi_spec()
        return jsonify(spec)

    @app.route("/api/rate-limit-status", methods=["GET"])
    @login_required
    def rate_limit_status() -> flask.Response:
        from cms.rate_limiting import get_api_rate_limit_status, get_rate_limit_status

        return jsonify(
            {
                "api_rate_limits": get_api_rate_limit_status(),
                "platform_rate_limits": get_rate_limit_status(),
            }
        )

    @app.route("/health")
    def health_check() -> flask.Response:
        from cms.models import Setting, db
        from cms.health_utils import check_external_services

        status = {"status": "ok"}
        http_status = 200

        quick = request.args.get("quick") == "1"
        if quick:
            # The dashboard polls this endpoint frequently; never run network
            # checks or write settings from the request worker.
            try:
                snapshot = json.loads(Setting.get("health_snapshot", ""))
                svc = snapshot.get("services", {})
                if not isinstance(svc, dict):
                    svc = {}
            except (TypeError, ValueError):
                svc = {}
            try:
                db.session.execute(db.text("SELECT 1"))
                svc["database"] = "ok"
            except Exception as exc:
                svc["database"] = f"error: {exc}"
        else:
            svc = check_external_services()
        relabel = {"ok": "connected"}
        database_label = relabel.get(
            svc.get("database", ""), svc.get("database", "unknown")
        )
        status["database"] = database_label
        status["spiderfoot"] = svc.get("spiderfoot", "unknown")
        status["spiderfoot_cached_ok"] = Setting.get("spiderfoot_last_ok", "never")
        for key in ("rdw", "kadaster", "hibp", "overheid", "brave", "tor"):
            if key in svc:
                status[key] = svc[key]

        from cms.cache import get_status as cache_status

        status["cache"] = cache_status()

        # Disk and memory checks (psutil optional — x86_64 binary on ARM macOS)
        try:
            import psutil

            disk = psutil.disk_usage("/")
            status["disk"] = {
                "total_gb": round(disk.total / (1024**3), 1),
                "used_gb": round(disk.used / (1024**3), 1),
                "free_gb": round(disk.free / (1024**3), 1),
                "percent_used": disk.percent,
            }
            if disk.percent > 90:
                status["status"] = "degraded"
                http_status = 503

            memory = psutil.virtual_memory()
            status["memory"] = {
                "total_gb": round(memory.total / (1024**3), 1),
                "available_gb": round(memory.available / (1024**3), 1),
                "percent_used": memory.percent,
            }
        except ImportError:
            pass

        # Redis check (when configured)
        redis_url = flask.current_app.config.get("REDIS_URL", "")
        if redis_url:
            try:
                import redis as redis_lib

                r = redis_lib.from_url(redis_url, socket_connect_timeout=3)
                r.ping()
                r.close()
                status["redis"] = "connected"
            except Exception:
                status["redis"] = "disconnected"
                status["status"] = "degraded"
                http_status = 503

        # Database health
        if svc.get("database") != "ok":
            status["status"] = "degraded"
            http_status = 503

        if not quick:
            try:
                from cms.models import db
                from sqlalchemy import text

                db.session.execute(text("SELECT 1"))
                status["db_ping"] = "ok"
            except Exception:
                status["db_ping"] = "error"
                status["status"] = "degraded"
                http_status = 503

            # Alembic migration sync (readiness)
            from cms.health_utils import check_migrations

            migrations = check_migrations()
            status["migrations"] = migrations
            if migrations != "ok":
                status["status"] = "degraded"
                http_status = 503

        return jsonify(status), http_status

    @app.errorhandler(404)
    def not_found_error(e) -> flask.Response:
        if request.path.startswith("/api/"):
            return api_error("Not found", 404)
        return render_template("cms/404.html"), 404

    @app.errorhandler(500)
    def internal_error(e) -> flask.Response:
        logger.exception("Internal server error")
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error"}), 500
        return render_template("cms/500.html"), 500

    @app.errorhandler(413)
    def request_entity_too_large(e) -> flask.Response:
        return jsonify({"error": "Request too large (max 16MB)"}), 413

    @app.route("/api/keep-alive", methods=["POST"])
    def session_keep_alive() -> flask.Response:
        """Extend the current session lifetime.

        Called periodically by the client-side session timeout warning JS.
        Returns the remaining session lifetime in seconds.
        """
        from flask import session

        session.modified = True
        remaining = int(app.permanent_session_lifetime.total_seconds())
        return jsonify({"status": "ok", "remaining": remaining})

    @app.before_request
    def log_request_info() -> None:
        request.request_id = getattr(g, "request_id", "-")
        if request.path.startswith("/api/"):
            logger.info("=> %s %s", request.method, request.path)

    @app.after_request
    def log_response_info(response) -> flask.Response:
        if request.path.startswith("/api/"):
            logger.info(
                "<= %s %s — %s", request.method, request.path, response.status_code
            )
        return response

    # Graceful shutdown
    def _handle_shutdown(signum, frame) -> None:
        logger.info("Received signal %s, shutting down...", signum)
        from cms.rate_limiting import cleanup_rate_limits

        cleanup_rate_limits(0)

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info("System routes registered")


def _check_ollama_available() -> bool:
    """Check if any AI provider (OpenRouter or Ollama) is available."""
    from cms.services.ai_service import check_ai_available

    return check_ai_available()


def _build_openapi_spec() -> dict:
    """Build OpenAPI 3.0.3 specification dynamically from Flask's URL map."""
    from flask import current_app

    paths = {}
    static_rules = {"static", "flask-compress.cache"}

    for rule in sorted(current_app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.rule.startswith("/static") or rule.endpoint in static_rules:
            continue
        endpoint_name = (
            rule.endpoint.split(".")[-1] if "." in rule.endpoint else rule.endpoint
        )
        methods = {
            m for m in rule.methods if m in ("GET", "POST", "PUT", "PATCH", "DELETE")
        }
        if not methods:
            continue
        path_item = paths.setdefault(rule.rule, {})
        for method in sorted(methods):
            method_lower = method.lower()
            path_item[method_lower] = {
                "summary": f"{method} {rule.rule}",
                "operationId": f"{endpoint_name}_{method_lower}",
                "parameters": [
                    {
                        "name": p,
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                    for p in _extract_path_params(rule.rule)
                ],
                "responses": {
                    "200": {"description": "Success"},
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                    "403": {"description": "Forbidden"},
                    "404": {"description": "Not found"},
                    "500": {"description": "Internal server error"},
                },
            }

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "OSINT Dashboard API",
            "version": "1.0.0",
            "description": "REST API for Iveras OSINT Dashboard — all endpoints auto-discovered from Flask routes.",
        },
        "servers": [{"url": "/", "description": "Local server"}],
        "paths": paths,
    }


def _extract_path_params(rule: str) -> list[str]:
    """Extract path parameter names from a Flask rule string."""
    import re

    return re.findall(r"<(?:\w+:)?(\w+)>", rule)
