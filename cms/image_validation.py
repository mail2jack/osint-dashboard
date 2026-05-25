"""
File upload validation
======================
Validates uploaded files by checking magic bytes (file signatures)
rather than trusting the client-supplied Content-Type header or extension.

Supported formats: PNG, JPEG, GIF, WebP, PDF, Office Open XML (docx/xlsx/pptx)
"""

import logging

logger = logging.getLogger(__name__)

_FILE_SIGNATURES = {
    b'\x89PNG\r\n\x1a\n': {'extensions': {'png'}, 'description': 'PNG image'},
    b'\xff\xd8\xff': {'extensions': {'jpg', 'jpeg'}, 'description': 'JPEG image'},
    b'GIF87a': {'extensions': {'gif'}, 'description': 'GIF image'},
    b'GIF89a': {'extensions': {'gif'}, 'description': 'GIF image'},
    b'%PDF': {'extensions': {'pdf'}, 'description': 'PDF document'},
    b'PK\x03\x04': {'extensions': {'docx', 'xlsx', 'pptx', 'zip'}, 'description': 'Office Open XML / ZIP'},
}

_WEBP_SIGNATURE = b'RIFF'


def identify_file_format(data: bytes) -> tuple[str, str]:
    """Identify file format by reading magic bytes.

    Returns (detected_format: str, description: str).
    detected_format is 'png', 'jpg', 'gif', 'webp', 'pdf', 'docx', etc. or ''.
    """
    if not data:
        return '', ''

    for sig, info in _FILE_SIGNATURES.items():
        if data[:len(sig)] == sig:
            exts = info['extensions']
            return next(iter(exts)), info['description']

    # WebP signature: RIFF....WEBP (bytes 0-3 = RIFF, bytes 8-11 = WEBP)
    if len(data) >= 12 and data[:4] == _WEBP_SIGNATURE and data[8:12] == b'WEBP':
        return 'webp', 'WebP image'

    return '', ''


def is_valid_file(data: bytes, expected_extension: str = None) -> tuple[bool, str]:
    """Check whether `data` matches a known file format.

    If `expected_extension` is provided, also checks that the detected
    format is compatible with that extension.

    Returns (is_valid: bool, detected_format: str).
    """
    detected_fmt, desc = identify_file_format(data)
    if not detected_fmt:
        return False, ''

    if expected_extension:
        expected_ext = expected_extension.lower().lstrip('.')
        # Check if the detected format matches the expected extension
        for sig, info in _FILE_SIGNATURES.items():
            if data[:len(sig)] == sig and expected_ext in info['extensions']:
                return True, detected_fmt
            if data[:len(sig)] == sig:
                continue
        # WebP
        if expected_ext in ('webp',) and detected_fmt == 'webp':
            return True, detected_fmt
        # PNG can also be .png
        if expected_ext == 'png' and detected_fmt == 'png':
            pass  # handled above

        return False, detected_fmt

    return True, detected_fmt


def validate_upload(file_storage, expected_extension: str = None) -> tuple[bool, str]:
    """Validate a Werkzeug FileStorage instance by reading its first 32 bytes.

    Args:
        file_storage: A Werkzeug FileStorage object (from request.files).
        expected_extension: The expected file extension (e.g. 'pdf', 'jpg').
                           If None, only checks that it's a known format.

    Returns (is_valid: bool, detected_format: str).
    The file cursor is left at the beginning after validation.
    """
    try:
        pos = file_storage.tell()
        data = file_storage.read(32)
        file_storage.seek(pos)
        return is_valid_file(data, expected_extension)
    except Exception as e:
        logger.warning(f"File validation error ({type(e).__name__}): {e}")
        return False, ''


# Legacy alias for backwards compatibility
def validate_image_file(file_storage) -> tuple[bool, str]:
    """Legacy: validate image file by magic bytes. Returns (is_valid, detected_format)."""
    return validate_upload(file_storage)

def is_valid_image(data: bytes) -> tuple[bool, str]:
    """Legacy: check if binary data is a known image format."""
    return is_valid_file(data)
