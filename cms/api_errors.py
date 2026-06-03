"""
Centralized API error handling for consistent JSON error responses.

Usage:
    raise APIError("Case not found", status_code=404)
    raise APIError("Invalid input", status_code=400, details={"field": "email"})
"""

from flask import jsonify


class APIError(Exception):
    """Exception that results in a JSON error response."""

    def __init__(
        self, message: str, status_code: int = 400, details: dict | list | None = None
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


def register_error_handlers(app) -> None:
    """Register APIError handler on the Flask app."""

    @app.errorhandler(APIError)
    def handle_api_error(error: APIError):
        payload = {"error": error.message}
        if error.details:
            payload["details"] = error.details
        return jsonify(payload), error.status_code

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": "Bad request"}), 400

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"error": "Forbidden"}), 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(429)
    def too_many_requests(e):
        return jsonify({"error": "Rate limit exceeded"}), 429

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({"error": "Internal server error"}), 500
