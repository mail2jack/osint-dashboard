"""
API v1 Blueprint — versioned REST endpoints.
"""

from flask import Blueprint, jsonify

api_v1_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


@api_v1_bp.route("/health")
def health():
    return jsonify({"status": "ok", "version": "1.0"})


@api_v1_bp.route("/version")
def version():
    from version import get_version

    return jsonify({"version": get_version(), "api_version": "1.0"})
