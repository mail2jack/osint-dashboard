"""Response helpers that match the existing JSON response patterns.

These are thin wrappers around ``jsonify`` that produce the same shapes the
codebase already uses — no breaking changes.

Add new helpers here as you spot common patterns instead of inlining jsonify().
"""

from typing import Any

from flask import jsonify


def api_success(data: dict, message: str | None = None, status: int = 200) -> Any:
    """Return a success response with optional message.

    Matches the common ``{"message": "...", "<entity>": {...}}`` pattern.
    ``data`` is merged directly (not wrapped in a ``data`` key).
    """
    body = dict(data)
    if message:
        body["message"] = message
    return jsonify(body), status


def api_created(data: dict, entity_name: str, message: str | None = None) -> Any:
    """Shorthand for 201 Created.  ``data`` is merged directly."""
    return api_success(data, message or f"{entity_name} created", status=201)


def api_deleted(entity_name: str = "Item") -> Any:
    """Shorthand for successful deletion."""
    return jsonify({"message": f"{entity_name} deleted"}), 200


def api_error(message: str, status: int = 400, extra: dict | None = None) -> Any:
    """Return an error response — same ``{"error": "..."}`` shape used everywhere."""
    body: dict[str, Any] = {"error": message}
    if extra:
        body.update(extra)
    return jsonify(body), status
