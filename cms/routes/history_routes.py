import logging

from flask import request, jsonify, Response as FlaskResponse
from flask_login import login_required

from .app_blueprint import app_routes_bp
from ..app_helpers import search_registry
from search_history import search_history
from .response import api_error

logger = logging.getLogger(__name__)


@app_routes_bp.route("/api/history", methods=["GET"])
@login_required
def get_history() -> FlaskResponse:
    return jsonify(search_history.get_history(limit=50))


@app_routes_bp.route("/api/archive", methods=["GET"])
@login_required
def get_archive() -> FlaskResponse:
    query = request.args.get("q", "")
    tool = request.args.get("tool", "")
    limit = int(request.args.get("limit", 100))
    return jsonify(
        search_history.get_archive(
            limit=limit, search_query=query, search_tool=tool if tool else None
        )
    )


@app_routes_bp.route("/api/history/archive/<entry_id>", methods=["POST"])
@login_required
def archive_entry(entry_id) -> FlaskResponse:
    search_history.archive_entry(entry_id)
    return jsonify({"success": True})


@app_routes_bp.route("/api/history/archive-all", methods=["POST"])
@login_required
def archive_all() -> FlaskResponse:
    count = search_history.archive_all()
    return jsonify({"success": True, "archived_count": count})


@app_routes_bp.route("/api/history/mark-read/<entry_id>", methods=["POST"])
@login_required
def mark_read(entry_id) -> FlaskResponse:
    search_history.mark_read(entry_id)
    return jsonify({"success": True})


@app_routes_bp.route("/api/history/mark-all-read", methods=["POST"])
@login_required
def mark_all_read() -> FlaskResponse:
    search_history.mark_all_read()
    return jsonify({"success": True})


@app_routes_bp.route("/api/history/stats", methods=["GET"])
@login_required
def get_history_stats() -> FlaskResponse:
    return jsonify(search_history.get_stats())


@app_routes_bp.route("/api/search/stop/<job_id>", methods=["POST"])
@login_required
def stop_search(job_id) -> FlaskResponse:
    if job_id in search_registry:
        search_registry[job_id].cancel()
        return jsonify({"success": True, "job_id": job_id})
    return jsonify({"success": False, "error": "Job not found"}), 404


@app_routes_bp.route("/api/search/progress/<job_id>", methods=["GET"])
def get_search_progress(job_id) -> FlaskResponse:
    if job_id in search_registry:
        job = search_registry[job_id]
        return jsonify(
            {
                "job_id": job_id,
                "cancelled": job.cancelled,
                "completed": job.completed,
                "progress": job.progress_state,
                "has_results": job.result is not None,
            }
        )
    return api_error("Job not found", 404)
