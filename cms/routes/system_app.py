"""
App-level system routes: health, version, config, docs, error handlers.
Registered directly on the Flask app (not the CMS blueprint).
"""

import logging
import signal

import flask
import httpx
from flask import Flask, request, jsonify, render_template, redirect, url_for, g

logger = logging.getLogger(__name__)


def register_system_routes(app: Flask) -> None:
    """Register system-level routes on the Flask app."""

    @app.route("/")
    def index() -> flask.Response:
        return redirect(url_for("cms.dashboard"))

    @app.route("/api/version", methods=["GET"])
    def get_version() -> flask.Response:
        from version import get_version_info

        return jsonify(get_version_info())

    @app.route("/api/changelog", methods=["GET"])
    def get_changelog() -> flask.Response:
        from version import get_version_info

        info = get_version_info()
        html_parts = [f"<h2>v{info['version']}</h2>"]
        if info.get("changes"):
            html_parts.append("<ul>")
            for change in info["changes"]:
                html_parts.append(f"<li>{change}</li>")
            html_parts.append("</ul>")
        if info.get("commit"):
            html_parts.append(
                f"<p style='color:var(--text-secondary);font-size:0.85rem;'>Commit: {info['commit'][:10]}</p>"
            )
        return jsonify({"html": "\n".join(html_parts)})

    @app.route("/api/config", methods=["GET"])
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
    def api_docs() -> str:
        return render_template("cms/api_docs.html")

    @app.route("/api/openapi.json")
    def openapi_spec() -> flask.Response:
        spec = _build_openapi_spec()
        return jsonify(spec)

    @app.route("/api/rate-limit-status", methods=["GET"])
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
        from cms.models import Setting
        from cms.spiderfoot_service import check_spiderfoot_health

        status = {"status": "ok", "database": "unknown", "spiderfoot": "unknown"}
        try:
            from cms import db

            db.session.execute(db.text("SELECT 1"))
            status["database"] = "connected"
        except Exception as e:
            status["database"] = f"error: {e}"
        # Check and cache SpiderFoot health
        try:
            healthy, msg = check_spiderfoot_health()
            status["spiderfoot"] = msg
        except Exception as e:
            status["spiderfoot"] = f"unavailable: {e}"
        # Cached SF status from last check
        status["spiderfoot_cached_ok"] = Setting.get("spiderfoot_last_ok", "never")
        # External service checks (skipped on quick check)
        if request.args.get("quick") != "1":
            for svc_name, svc_url, svc_check in [
                (
                    "rdw",
                    "https://opendata.rdw.nl/resource/m9d7-ebf2.json",
                    lambda r: r.status_code in (200, 401, 403),
                ),
                (
                    "kadaster",
                    "https://geodata.nationaalgeoregister.nl/locatieserver/free",
                    lambda r: r.status_code == 200,
                ),
                ("hibp", "https://haveibeenpwned.com", lambda r: r.status_code == 200),
            ]:
                try:
                    r = httpx.get(svc_url, timeout=5)
                    status[svc_name] = (
                        "ok" if svc_check(r) else f"unexpected: {r.status_code}"
                    )
                except Exception as e:
                    status[svc_name] = f"unavailable: {e}"
        from cms.cache import get_status as cache_status

        status["cache"] = cache_status()
        return jsonify(status)

    @app.errorhandler(404)
    def not_found_error(e) -> flask.Response:
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found"}), 404
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
    """Check if Ollama AI service is available."""
    from cms.services.ai_service import get_ollama_config

    config = get_ollama_config()
    if isinstance(config, tuple):
        url, _model = config
    else:
        url = config.get("url") if isinstance(config, dict) else ""
    if not url:
        return False
    try:
        import httpx

        r = httpx.get(f"{url}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _build_openapi_spec() -> dict:
    """Build OpenAPI 3.0.3 specification."""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "OSINT Dashboard API",
            "version": "1.0.0",
            "description": "API for OSINT lookups and automations.",
        },
        "servers": [{"url": "/", "description": "Local server"}],
        "paths": {
            "/api/email": {
                "post": {
                    "summary": "Email lookup",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/EmailQuery"}
                            }
                        },
                    },
                    "responses": {"200": {"description": "Email lookup results"}},
                }
            },
            "/api/ip": {
                "post": {
                    "summary": "IP address lookup",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/IPQuery"}
                            }
                        },
                    },
                    "responses": {"200": {"description": "IP lookup results"}},
                }
            },
            "/api/domain": {
                "post": {
                    "summary": "Domain WHOIS/DNS lookup",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/DomainQuery"}
                            }
                        },
                    },
                    "responses": {"200": {"description": "Domain lookup results"}},
                }
            },
            "/api/openkvk": {
                "post": {
                    "summary": "Dutch business registry lookup",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/OpenKVKQuery"}
                            }
                        },
                    },
                    "responses": {"200": {"description": "Company info"}},
                }
            },
            "/api/webcam": {
                "post": {
                    "summary": "Webcam search",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/WebcamQuery"}
                            }
                        },
                    },
                    "responses": {"200": {"description": "Webcam results"}},
                }
            },
            "/api/hibp": {
                "post": {
                    "summary": "Have I Been Pwned check",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/HIBPQuery"}
                            }
                        },
                    },
                    "responses": {"200": {"description": "Breach results"}},
                }
            },
            "/api/rate-limit-status": {
                "get": {
                    "summary": "Get rate limit status",
                    "responses": {"200": {"description": "Rate limit info"}},
                }
            },
        },
        "components": {
            "schemas": {
                "EmailQuery": {
                    "type": "object",
                    "required": ["email"],
                    "properties": {"email": {"type": "string", "format": "email"}},
                },
                "IPQuery": {
                    "type": "object",
                    "required": ["ip"],
                    "properties": {"ip": {"type": "string", "format": "ipv4"}},
                },
                "DomainQuery": {
                    "type": "object",
                    "required": ["domain"],
                    "properties": {"domain": {"type": "string"}},
                },
                "OpenKVKQuery": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {"query": {"type": "string"}},
                },
                "WebcamQuery": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "country": {"type": "string"},
                    },
                },
                "HIBPQuery": {
                    "type": "object",
                    "required": ["email"],
                    "properties": {"email": {"type": "string", "format": "email"}},
                },
            }
        },
    }
