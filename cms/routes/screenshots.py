import logging
import os
import uuid
import io

from flask import request, jsonify, current_app, abort, send_file
from flask_login import login_required, current_user
from PIL import Image
from werkzeug.utils import secure_filename

from . import cms_bp
from ..models import db, Case, Screenshot, AuditLog
from ..auth import case_access_required, case_edit_required

logger = logging.getLogger(__name__)

UPLOAD_FOLDER = 'uploads'
SCREENSHOT_FOLDER = 'screenshots'


def get_screenshot_path(case_id: str, filename: str = None) -> str:
    """Get the path for screenshot storage."""
    base_path = os.path.join(current_app.root_path, 'static',
                             UPLOAD_FOLDER, 'cases', case_id, SCREENSHOT_FOLDER)
    if filename:
        return os.path.join(base_path, filename)
    return base_path


@cms_bp.route('/cases/<case_id>/screenshots')
@login_required
@case_access_required
def list_screenshots(case_id: str):
    """List all screenshots for a case."""
    db.session.get(Case, case_id) or abort(404)
    screenshots = Screenshot.query.filter_by(
        case_id=case_id).order_by(Screenshot.created_at.desc()).all()

    return jsonify({
        'screenshots': [s.to_dict() for s in screenshots],
        'count': len(screenshots)
    })


@cms_bp.route('/cases/<case_id>/screenshots/upload', methods=['POST'])
@login_required
@case_access_required
@case_edit_required
def upload_screenshot(case_id: str):
    """Upload a screenshot file for a case."""
    db.session.get(Case, case_id) or abort(404)

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    # Check file type
    if not file.content_type or not file.content_type.startswith('image/'):
        return jsonify({'error': 'File must be an image'}), 400

    # Create screenshot directory
    screenshot_dir = get_screenshot_path(case_id)
    os.makedirs(screenshot_dir, exist_ok=True)

    # Generate unique filename
    screenshot_id = str(uuid.uuid4())

    # Get file extension from original filename or content type
    original_ext = file.filename.rsplit(
        '.', 1)[-1].lower() if '.' in file.filename else 'png'
    if original_ext not in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
        original_ext = 'png'

    filename = f"{screenshot_id}.{original_ext}"
    filepath = os.path.join(screenshot_dir, filename)

    # Initialize filepath to avoid unbound variable in except block
    filepath_defined = False

    try:
        # Read file content into memory first
        file_content = file.read()

        # Write to file
        with open(filepath, 'wb') as f:
            f.write(file_content)

        filepath_defined = True
        file_size = os.path.getsize(filepath)

        # Get URL from form
        url = request.form.get('url', '')

        # Create database record
        screenshot = Screenshot(
            id=screenshot_id,
            case_id=case_id,
            url=url,
            filename=filename,
            title=url.split(
                '/')[-1][:300] if url else f'Screenshot {screenshot_id[:8]}',
            file_size=file_size,
            created_by=current_user.id
        )

        db.session.add(screenshot)

        # Log the action
        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='screenshot',
            entity_id=screenshot_id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Uploaded screenshot: {url or 'No URL'}"
        )

        db.session.commit()

        return jsonify({
            'message': 'Screenshot uploaded successfully',
            'screenshot': screenshot.to_dict()
        }), 201

    except Exception as e:
        logger.error(f"Screenshot upload error ({type(e).__name__}): {e}")
        # Clean up file if it was created
        if filepath_defined and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                logger.debug("Failed to clean up screenshot file after upload error")
        return jsonify({'error': str(e)}), 500


@cms_bp.route('/cases/<case_id>/screenshots/capture', methods=['POST'])
@login_required
@case_access_required
@case_edit_required
def capture_screenshot(case_id: str):
    """
    Capture a screenshot of a URL and save it.
    Note: This requires Playwright or similar to be installed.
    For now, this returns an error indicating the feature needs setup.
    """
    db.session.get(Case, case_id) or abort(404)
    data = request.get_json()

    if not data or not data.get('url'):
        return jsonify({'error': 'URL is required'}), 400

    url = data.get('url')
    title = data.get('title', '')

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
                page = browser.new_page(
                    viewport={'width': 1280, 'height': 720})
                page.goto(url, wait_until='networkidle', timeout=30000)
                page.wait_for_timeout(2000)  # Extra wait for dynamic content
                page.screenshot(path=filepath, full_page=False)

                # Get page title if not provided
                if not title:
                    title = page.title()[:300]

                browser.close()

            file_size = os.path.getsize(filepath)

        except ImportError:
            # Playwright not installed - try selenium as fallback
            return jsonify({
                'error': 'Screenshot capture not available. No screenshot library installed.',
                'setup_required': True,
                'message': 'Install playwright: pip install playwright && playwright install chromium'
            }), 503

        except Exception as e:
            logger.error(f"Playwright capture failed ({type(e).__name__}): {e}")
            return jsonify({
                'error': f'Failed to capture screenshot: {str(e)}',
                'setup_required': False
            }), 500

        # Create database record
        screenshot = Screenshot(
            id=screenshot_id,
            case_id=case_id,
            url=url,
            filename=filename,
            title=title,
            file_size=file_size,
            created_by=current_user.id
        )

        db.session.add(screenshot)

        # Log the action
        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='screenshot',
            entity_id=screenshot_id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Captured screenshot from: {url}"
        )

        db.session.commit()

        return jsonify({
            'message': 'Screenshot captured successfully',
            'screenshot': screenshot.to_dict()
        }), 201

    except Exception as e:
        logger.error(f"Screenshot capture error ({type(e).__name__}): {e}")
        # Clean up file if database insert failed
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({'error': str(e)}), 500


@cms_bp.route('/cases/<case_id>/screenshots/<screenshot_id>/thumbnail')
@login_required
@case_access_required
def get_screenshot_thumbnail(case_id: str, screenshot_id: str):
    """Get a thumbnail version of a screenshot."""
    screenshot = Screenshot.query.filter_by(
        id=screenshot_id, case_id=case_id).first()

    if not screenshot:
        return '', 404

    filepath = get_screenshot_path(case_id, screenshot.filename)

    if not os.path.exists(filepath):
        return '', 404

    try:
        # First try to generate a proper thumbnail
        try:
            with Image.open(filepath) as img:
                img.thumbnail((200, 200), Image.Resampling.LANCZOS)
                thumb_io = io.BytesIO()
                img.save(thumb_io, format='PNG')
            thumb_io.seek(0)
            return send_file(
                thumb_io,
                mimetype='image/png',
                as_attachment=False
            )
        except Exception as e:
            logger.warning(
                f"Thumbnail generation failed, serving original ({type(e).__name__}): {e}")
            # Fallback: serve original image
            return send_file(filepath, mimetype='image/png', as_attachment=False)
    except Exception as e:
        logger.error(f"Thumbnail error ({type(e).__name__}): {e}")
        return '', 500


@cms_bp.route('/cases/<case_id>/screenshots/<screenshot_id>/view')
@login_required
@case_access_required
def view_screenshot(case_id: str, screenshot_id: str):
    """View the full screenshot."""
    screenshot = Screenshot.query.filter_by(
        id=screenshot_id, case_id=case_id).first()

    if not screenshot:
        return '', 404

    filepath = get_screenshot_path(case_id, screenshot.filename)

    if not os.path.exists(filepath):
        return '', 404

    try:
        # Detect mimetype from file extension
        ext = screenshot.filename.rsplit(
            '.', 1)[-1].lower() if '.' in screenshot.filename else 'png'
        mimetype_map = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }
        mimetype = mimetype_map.get(ext, 'image/png')

        return send_file(
            filepath,
            mimetype=mimetype,
            as_attachment=False,
            download_name=screenshot.title or screenshot.filename
        )
    except Exception as e:
        logger.error(f"View screenshot error ({type(e).__name__}): {e}")
        return '', 500


@cms_bp.route('/cases/<case_id>/screenshots/<screenshot_id>')
@login_required
@case_access_required
def get_screenshot(case_id: str, screenshot_id: str):
    """Get screenshot details."""
    screenshot = Screenshot.query.filter_by(
        id=screenshot_id, case_id=case_id).first()

    if not screenshot:
        return jsonify({'error': 'Screenshot not found'}), 404

    return jsonify(screenshot.to_dict())


@cms_bp.route('/cases/<case_id>/screenshots/<screenshot_id>', methods=['DELETE'])
@login_required
@case_access_required
@case_edit_required
def delete_screenshot(case_id: str, screenshot_id: str):
    """Delete a screenshot."""
    screenshot = Screenshot.query.filter_by(
        id=screenshot_id, case_id=case_id).first()

    if not screenshot:
        return jsonify({'error': 'Screenshot not found'}), 404

    try:
        # Delete the file
        filepath = get_screenshot_path(case_id, screenshot.filename)
        if os.path.exists(filepath):
            os.remove(filepath)

        # Log the action
        AuditLog.log(
            user_id=current_user.id,
            action='delete',
            entity_type='screenshot',
            entity_id=screenshot_id,
            ip_address=request.remote_addr,
            case_id=case_id,
            description=f"Deleted screenshot: {screenshot.title or screenshot.filename}"
        )

        # Delete database record
        db.session.delete(screenshot)
        db.session.commit()

        return jsonify({'message': 'Screenshot deleted'}), 200

    except Exception as e:
        logger.error(f"Screenshot delete error ({type(e).__name__}): {e}")
        return jsonify({'error': str(e)}), 500
