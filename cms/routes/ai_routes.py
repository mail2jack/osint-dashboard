import logging

from flask import request, jsonify, Response as FlaskResponse

from .app_blueprint import app_routes_bp
from .. import csrf
from cms.api_key_auth import api_key_required
from cms.rate_limiting import rate_limit, DEFAULT_RATE_LIMIT
from cms.feature_flags import tool_enabled
from cms.validation import (
    validate,
    AISummarizeSchema,
    AIAnalyzeQuerySchema,
    AIEnrichProfileSchema,
)
from cms.services.ai_service import (
    get_ai_config,
    summarize_results,
    analyze_natural_language,
    enrich_profile,
)
from ..app_helpers import check_ollama_available

logger = logging.getLogger(__name__)


@app_routes_bp.route("/api/ai/status", methods=["GET"])
def ai_status() -> FlaskResponse:
    config = get_ai_config()
    return jsonify(
        {
            "available": config["available"],
            "provider": config["provider"] if config["available"] else None,
            "model": config["model"] if config["available"] else None,
            "message": f"AI features ready ({config['provider']})"
            if config["available"]
            else "No AI provider configured. Set up OpenRouter API key or install Ollama.",
        }
    )


@app_routes_bp.route("/api/ai/summarize", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("ai")
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="ai_summarize")
@validate(AISummarizeSchema)
def ai_summarize() -> FlaskResponse:
    from ..tier_limits import check_feature

    if not check_feature("ai"):
        return jsonify(
            {
                "error": "AI features are not available on your current plan. Upgrade to access AI."
            }
        ), 403
    query = request.validated_data.get("query", "")
    tool = request.validated_data.get("tool", "unknown")
    findings = request.validated_data.get("findings", [])

    if not check_ollama_available():
        return jsonify({"error": "Ollama not available"}), 503

    summary = summarize_results(query, tool, findings)
    return jsonify({"summary": summary})


@app_routes_bp.route("/api/ai/analyze-query", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("ai")
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="ai_analyze")
@validate(AIAnalyzeQuerySchema)
def ai_analyze_query() -> FlaskResponse:
    from ..tier_limits import check_feature

    if not check_feature("ai"):
        return jsonify(
            {
                "error": "AI features are not available on your current plan. Upgrade to access AI."
            }
        ), 403
    user_query = request.validated_data.get("query", "")

    if not check_ollama_available():
        return jsonify({"error": "Ollama not available"}), 503

    available_tools = [
        "social",
        "email",
        "username",
        "maigret",
        "phone",
        "person",
        "ip",
        "domain",
    ]
    result = analyze_natural_language(user_query, available_tools)
    return jsonify(result)


@app_routes_bp.route("/api/ai/enrich-profile", methods=["POST"])
@csrf.exempt
@api_key_required
@tool_enabled("ai")
@rate_limit(limit=DEFAULT_RATE_LIMIT, key_prefix="ai_enrich")
@validate(AIEnrichProfileSchema)
def ai_enrich_profile() -> FlaskResponse:
    from ..tier_limits import check_feature

    if not check_feature("ai"):
        return jsonify(
            {
                "error": "AI features are not available on your current plan. Upgrade to access AI."
            }
        ), 403
    platform = request.validated_data.get("platform", "Unknown")
    username = request.validated_data.get("username", "")
    info = request.validated_data.get("info", {})

    if not check_ollama_available():
        return jsonify({"error": "Ollama not available"}), 503

    analysis = enrich_profile(platform, username, info)
    return jsonify({"analysis": analysis})
