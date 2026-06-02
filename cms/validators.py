import re
import socket

from cms.constants import FALSE_POSITIVE_PATTERNS


def verify_profile(response_text, username, url=None):
    if not response_text:
        return "unconfirmed"
    text_lower = response_text.lower()
    username_lower = username.lower()
    for pattern, _ in FALSE_POSITIVE_PATTERNS:
        if pattern.search(text_lower):
            return "likely_false"
    if username_lower in text_lower:
        return "verified"
    url_lower = url.lower() if url else ""
    if username_lower in url_lower:
        return "verified"
    generic_patterns = [
        r"(welcome|home|landing).{0,50}(page|site)",
        r"(log in|sign in|register|sign up).{0,30}(now|today)",
        r"^<!doctype html>\s*<html>\s*<head>\s*<title>\s*</title>",
    ]
    for pattern in generic_patterns:
        if re.search(pattern, text_lower):
            if len(response_text) < 500:
                return "likely_false"
    return "unconfirmed"


def validate_email(email):
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None


def validate_ip(ip):
    try:
        socket.inet_aton(ip)
        return True
    except socket.error:
        return False


def validate_domain(domain):
    pattern = r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,61}[a-zA-Z0-9]?\.[a-zA-Z]{2,}$"
    return re.match(pattern, domain) is not None


def interpolate_string(input_object, username):
    if isinstance(input_object, str):
        return input_object.replace("{}", username.replace(" ", "%20"))
    elif isinstance(input_object, dict):
        return {k: interpolate_string(v, username) for k, v in input_object.items()}
    elif isinstance(input_object, list):
        return [interpolate_string(i, username) for i in input_object]
    return input_object


def normalize_phone_number(phone: str) -> str:
    if not phone:
        return phone
    cleaned = re.sub(r"[^\d+]", "", phone)
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if cleaned.startswith("00"):
        cleaned = cleaned[2:]
    elif cleaned.startswith("0"):
        cleaned = cleaned[1:]
    return cleaned


__all__ = [
    "verify_profile",
    "validate_email",
    "validate_ip",
    "validate_domain",
    "interpolate_string",
    "normalize_phone_number",
]
