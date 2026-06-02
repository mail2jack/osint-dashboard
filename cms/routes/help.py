"""
Help & Documentation Routes
============================
Context-sensitive help pages and API endpoint for the slide-out help panel.
Help content is stored as Markdown files in the help/ directory.
"""

import logging
from pathlib import Path

import markdown
import flask
from flask import jsonify, render_template
from flask_login import login_required

from . import cms_bp

logger = logging.getLogger(__name__)

HELP_DIR = Path(__file__).resolve().parent.parent.parent / "help"
ALLOWED_TOPICS = {
    "general",
    "dashboard",
    "cases",
    "clients",
    "subjects",
    "spiderfoot",
    "search",
    "settings",
    "reminders",
    "exports",
    "financials",
    "comments",
    "audit",
    "social",
    "lookups",
    "users",
}


def _load_help(topic: str) -> tuple[str | None, str | None]:
    """Load help content for a topic.

    Returns:
        Tuple of (html_content, error_message). One will be None.
    """
    if topic not in ALLOWED_TOPICS:
        return None, f'Help topic "{topic}" not found'

    md_file = HELP_DIR / f"{topic}.md"
    if not md_file.exists():
        return None, f'Help file for "{topic}" not found'

    try:
        md_text = md_file.read_text(encoding="utf-8")
        html = markdown.markdown(md_text, extensions=["extra", "toc", "codehilite"])
        return html, None
    except Exception as e:
        logger.error(f"Failed to load help/{topic}.md: {e}")
        return None, f"Error loading help content: {e}"


@cms_bp.route("/help")
@login_required
def help_index() -> str:
    """Full help overview page."""
    topics = []
    for fname in sorted(HELP_DIR.glob("*.md")):
        topic = fname.stem
        if topic in ALLOWED_TOPICS:
            # Extract first heading as title
            content = fname.read_text(encoding="utf-8")
            title = topic.capitalize()
            for line in content.splitlines():
                if line.startswith("# "):
                    title = line.lstrip("# ")
                    break
            topics.append({"id": topic, "title": title})
    return render_template("cms/help.html", topics=topics)


@cms_bp.route("/api/help/<topic>")
@login_required
def help_api(topic: str) -> flask.Response:
    """API endpoint — returns HTML for the slide-out panel (AJAX)."""
    html, error = _load_help(topic)
    if error:
        return jsonify({"error": error}), 404
    return jsonify({"html": html, "topic": topic})


@cms_bp.route("/help/<topic>")
@login_required
def help_page(topic: str) -> str:
    """Full help page for a specific topic (non-JS fallback)."""
    html, error = _load_help(topic)
    if error:
        flask.abort(404)
    return render_template("cms/help.html", topic_html=html, topic_id=topic)
