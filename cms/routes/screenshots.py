import logging
import os
import uuid
import io

import flask
from flask import request, jsonify, current_app, abort, send_file
from flask_login import login_required, current_user
from PIL import Image

from . import cms_bp
from ..validation import validate, CaptureScreenshotSchema, ScreenshotUploadSchema
from ..models import db, Case, Screenshot, AuditLog
from ..auth import case_access_required, case_edit_required, apply_tenant_filter
from ..image_validation import validate_image_file
from ..tier_limits import check_storage_limit

from .response import api_success, api_error

logger = logging.getLogger(__name__)

UPLOAD_FOLDER = "uploads"
SCREENSHOT_FOLDER = "screenshots"


def get_screenshot_path(case_id: str, filename: str = None) -> str:
    """Get the path for screenshot storage."""
    base_path = os.path.join(
        current_app.root_path,
        "static",
        UPLOAD_FOLDER,
        "cases",
        case_id,
        SCREENSHOT_FOLDER,
    )
    if filename:
        return os.path.join(base_path, filename)
    return base_path


@cms_bp.route("/cases/<case_id>/screenshots")
@login_required
@case_access_required
def list_screenshots(case_id: str) -> str:
    """List all screenshots for a case."""
    db.session.get(Case, case_id) or abort(404)
    query = Screenshot.query.filter_by(case_id=case_id)
    query = apply_tenant_filter(query, Screenshot)
    screenshots = query.order_by(Screenshot.created_at.desc()).all()

    return jsonify(
        {"screenshots": [s.to_dict() for s in screenshots], "count": len(screenshots)}
    )


@cms_bp.route("/cases/<case_id>/screenshots/upload", methods=["POST"])
@login_required
@case_access_required
@case_edit_required
@validate(ScreenshotUploadSchema)
def upload_screenshot(case_id: str) -> flask.Response:
    """Upload a screenshot file for a case."""
    db.session.get(Case, case_id) or abort(404)

    if "file" not in request.files:
        return api_error("No file provided", 400)

    file = request.files["file"]

    if file.filename == "":
        return api_error("No file selected", 400)

    # Check file type via magic bytes
    is_img, _ = validate_image_file(file)
    if not is_img:
        return jsonify(
            {"error": "File must be an image (PNG, JPEG, GIF, or WebP)"}
        ), 400

    # Check storage quota before saving
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    ok, used_mb, max_mb = check_storage_limit(
        current_user.tenant_id, extra_bytes=file_size
    )
    if not ok:
        return api_error(
            f"Storage limit reached ({used_mb}/{max_mb} MB). Upgrade your plan to upload more files.",
            403,
        )

    # Create screenshot directory
    screenshot_dir = get_screenshot_path(case_id)
    os.makedirs(screenshot_dir, exist_ok=True)

    # Generate unique filename
    screenshot_id = str(uuid.uuid4())

    # Get file extension from original filename or content type
    original_ext = (
        file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "png"
    )
    if original_ext not in ["png", "jpg", "jpeg", "gif", "webp"]:
        original_ext = "png"

    filename = f"{screenshot_id}.{original_ext}"
    filepath = os.path.join(screenshot_dir, filename)

    # Initialize filepath to avoid unbound variable in except block
    filepath_defined = False

    try:
        # Read file content into memory first
        file_content = file.read()

        # Write to file
        with open(filepath, "wb") as f:
            f.write(file_content)

        filepath_defined = True
        file_size = os.path.getsize(filepath)

        # Get URL from form
        url = request.validated_data.get("url", "")

        # Create database record
        screenshot = Screenshot(
            id=screenshot_id,
            case_id=case_id,
            url=url,
            filename=filename,
            title=url.split("/")[-1][:300]
            if url
            else f"Screenshot {screenshot_id[:8]}",
            file_size=file_size,
            created_by=current_user.id,
        )

        db.session.add(screenshot)

        # Log the action
        AuditLog.log(
            user_id=current_user.id,
            action="create",
            entity_type="screenshot",
            entity_id=screenshot_id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Uploaded screenshot: {url or 'No URL'}",
        )

        db.session.commit()

        return jsonify(
            {
                "message": "Screenshot uploaded successfully",
                "screenshot": screenshot.to_dict(),
            }
        ), 201

    except Exception:
        logger.exception("Screenshot upload error")
        # Clean up file if it was created
        if filepath_defined and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                logger.debug("Failed to clean up screenshot file after upload error")
        return jsonify({"error": "Internal server error"}), 500


@cms_bp.route("/cases/<case_id>/screenshots/capture", methods=["POST"])
@login_required
@case_access_required
@case_edit_required
@validate(CaptureScreenshotSchema)
def capture_screenshot(case_id: str) -> flask.Response:
    """
    Capture a screenshot of a URL and save it.
    Note: This requires Playwright or similar to be installed.
    For now, this returns an error indicating the feature needs setup.
    """
    db.session.get(Case, case_id) or abort(404)
    data = request.validated_data

    if not data or not data.get("url"):
        return api_error("URL is required", 400)

    url = data.get("url")
    title = data.get("title", "")

    # Create screenshot directory
    screenshot_dir = get_screenshot_path(case_id)
    os.makedirs(screenshot_dir, exist_ok=True)

    # Generate unique filename
    screenshot_id = str(uuid.uuid4())
    filename = f"{screenshot_id}.png"
    filepath = os.path.join(screenshot_dir, filename)

    try:
        # Try to use Playwright for screenshot capture
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 720})
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)  # Extra wait for dynamic content
                page.screenshot(path=filepath, full_page=False)

                # Get page title if not provided
                if not title:
                    title = page.title()[:300]

                browser.close()

            file_size = os.path.getsize(filepath)

            # Check storage quota after capturing
            ok, used_mb, max_mb = check_storage_limit(
                current_user.tenant_id, extra_bytes=file_size
            )
            if not ok:
                os.remove(filepath)
                return api_error(
                    f"Storage limit reached ({used_mb}/{max_mb} MB). Upgrade your plan to capture more screenshots.",
                    403,
                )

        except ImportError:
            # Playwright not installed - try selenium as fallback
            return jsonify(
                {
                    "error": "Screenshot capture not available. No screenshot library installed.",
                    "setup_required": True,
                    "message": "Install playwright: pip install playwright && playwright install chromium",
                }
            ), 503

        except Exception:
            logger.exception("Playwright capture failed")
            return jsonify(
                {
                    "error": "Failed to capture screenshot",
                    "setup_required": False,
                }
            ), 500

        # Create database record
        screenshot = Screenshot(
            id=screenshot_id,
            case_id=case_id,
            url=url,
            filename=filename,
            title=title,
            file_size=file_size,
            created_by=current_user.id,
        )

        db.session.add(screenshot)

        # Log the action
        AuditLog.log(
            user_id=current_user.id,
            action="create",
            entity_type="screenshot",
            entity_id=screenshot_id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Captured screenshot from: {url}",
        )

        db.session.commit()

        return jsonify(
            {
                "message": "Screenshot captured successfully",
                "screenshot": screenshot.to_dict(),
            }
        ), 201

    except Exception:
        logger.exception("Screenshot capture error")
        # Clean up file if database insert failed
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({"error": "Internal server error"}), 500


@cms_bp.route("/cases/<case_id>/screenshots/<screenshot_id>/thumbnail")
@login_required
@case_access_required
def get_screenshot_thumbnail(case_id: str, screenshot_id: str) -> flask.Response:
    """Get a thumbnail version of a screenshot."""
    screenshot = Screenshot.query.filter_by(id=screenshot_id, case_id=case_id).first()

    if not screenshot:
        return "", 404

    filepath = get_screenshot_path(case_id, screenshot.filename)

    if not os.path.exists(filepath):
        return "", 404

    try:
        # First try to generate a proper thumbnail
        try:
            with Image.open(filepath) as img:
                img.thumbnail((200, 200), Image.Resampling.LANCZOS)
                thumb_io = io.BytesIO()
                img.save(thumb_io, format="PNG")
            thumb_io.seek(0)
            return send_file(thumb_io, mimetype="image/png", as_attachment=False)
        except Exception as e:
            logger.warning(
                f"Thumbnail generation failed, serving original ({type(e).__name__}): {e}"
            )
            # Fallback: serve original image
            return send_file(filepath, mimetype="image/png", as_attachment=False)
    except Exception as e:
        logger.error(f"Thumbnail error ({type(e).__name__}): {e}")
        return "", 500


@cms_bp.route("/cases/<case_id>/screenshots/<screenshot_id>/view")
@login_required
@case_access_required
def view_screenshot(case_id: str, screenshot_id: str) -> str:
    """View the full screenshot."""
    screenshot = Screenshot.query.filter_by(id=screenshot_id, case_id=case_id).first()

    if not screenshot:
        return "", 404

    filepath = get_screenshot_path(case_id, screenshot.filename)

    if not os.path.exists(filepath):
        return "", 404

    try:
        # Detect mimetype from file extension
        ext = (
            screenshot.filename.rsplit(".", 1)[-1].lower()
            if "." in screenshot.filename
            else "png"
        )
        mimetype_map = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        mimetype = mimetype_map.get(ext, "image/png")

        return send_file(
            filepath,
            mimetype=mimetype,
            as_attachment=False,
            download_name=screenshot.title or screenshot.filename,
        )
    except Exception as e:
        logger.error(f"View screenshot error ({type(e).__name__}): {e}")
        return "", 500


@cms_bp.route("/cases/<case_id>/screenshots/<screenshot_id>")
@login_required
@case_access_required
def get_screenshot(case_id: str, screenshot_id: str) -> flask.Response:
    """Get screenshot details."""
    screenshot = Screenshot.query.filter_by(id=screenshot_id, case_id=case_id).first()

    if not screenshot:
        return api_error("Screenshot not found", 404)

    return jsonify(screenshot.to_dict())


@cms_bp.route("/cases/<case_id>/screenshots/<screenshot_id>", methods=["DELETE"])
@login_required
@case_access_required
@case_edit_required
def delete_screenshot(case_id: str, screenshot_id: str) -> flask.Response:
    """Delete a screenshot."""
    screenshot = Screenshot.query.filter_by(id=screenshot_id, case_id=case_id).first()

    if not screenshot:
        return api_error("Screenshot not found", 404)

    try:
        # Delete the file
        filepath = get_screenshot_path(case_id, screenshot.filename)
        if os.path.exists(filepath):
            os.remove(filepath)

        # Log the action
        AuditLog.log(
            user_id=current_user.id,
            action="delete",
            entity_type="screenshot",
            entity_id=screenshot_id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Deleted screenshot: {screenshot.title or screenshot.filename}",
        )

        # Delete database record
        db.session.delete(screenshot)
        db.session.commit()

        return api_success({}, "Screenshot deleted")

    except Exception:
        logger.exception("Screenshot delete error")
        return jsonify({"error": "Internal server error"}), 500
