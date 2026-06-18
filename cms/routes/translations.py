import json
import logging
import os
import re
import subprocess

from flask import current_app, request, jsonify, render_template
from flask_login import login_required

from . import cms_bp
from ..models import Setting
from ..services.ai_service import (
    get_openrouter_config,
    check_openrouter_detailed,
    check_ollama_available,
    get_openrouter_error,
)

from .response import api_error

logger = logging.getLogger(__name__)


def _get_review():
    raw = Setting.get("translation_review")
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _save_review(data):
    Setting.set("translation_review", json.dumps(data))


def _update_po_entry(filepath, msgid, new_msgstr):
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    result = []
    current_msgid = None
    in_entry = False

    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("msgid "):
            current_msgid = None
            in_entry = False
            m = re.match(r'msgid "(.*)"\s*$', line)
            current_msgid = m.group(1) if m else ""
            if current_msgid == "":
                j = i + 1
                parts = []
                while j < len(lines) and lines[j].startswith('"'):
                    m2 = re.match(r'"(.*)"\s*$', lines[j])
                    if m2:
                        parts.append(m2.group(1))
                    j += 1
                current_msgid = "".join(parts)
            in_entry = current_msgid == msgid
            result.append(line)
            i += 1
            continue

        if in_entry and line.startswith("msgstr "):
            escaped = new_msgstr.replace("\\", "\\\\").replace('"', '\\"')
            result.append(f'msgstr "{escaped}"\n')
            i += 1
            while i < len(lines) and lines[i].startswith('"'):
                i += 1
            in_entry = False
            continue

        result.append(line)
        i += 1

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(result)


def _parse_po(filepath):
    entries = []
    state = None
    key = None
    key_buf = []
    str_buf = []

    def flush():
        nonlocal key, key_buf, str_buf
        if key is not None:
            entries.append(
                {
                    "msgid": key + "".join(key_buf),
                    "msgstr": "".join(str_buf),
                }
            )
        key = None
        key_buf = []
        str_buf = []

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                if key is not None:
                    flush()
                state = None
                continue
            if line.startswith("msgid "):
                flush()
                m = re.match(r'msgid "(.*)"\s*$', line)
                key = m.group(1) if m else ""
                state = "msgid"
            elif line.startswith("msgstr "):
                m = re.match(r'msgstr "(.*)"\s*$', line)
                if m:
                    str_buf = [m.group(1)]
                else:
                    str_buf = []
                state = "msgstr"
            elif line.startswith('"') and state == "msgid":
                m = re.match(r'"(.*)"\s*$', line)
                if m:
                    key_buf.append(m.group(1))
            elif line.startswith('"') and state == "msgstr":
                m = re.match(r'"(.*)"\s*$', line)
                if m:
                    str_buf.append(m.group(1))
            elif key is not None and not line.strip():
                flush()
                state = None
    flush()
    return entries


@cms_bp.route("/api/translations/ai-status")
@login_required
def translation_ai_status():
    """Return AI provider status with error details for the translations page."""
    or_config = get_openrouter_config()
    or_detailed = check_openrouter_detailed()
    ollama_avail = check_ollama_available()
    last_error = get_openrouter_error()

    return jsonify(
        {
            "openrouter": {
                "configured": bool(or_config["api_key"]),
                "model": or_config["model"],
                "available": or_detailed["available"],
                "reason": or_detailed.get("reason"),
            },
            "ollama": {
                "available": ollama_avail,
            },
            "last_error": last_error,
            "any_available": or_detailed["available"] or ollama_avail,
        }
    )


@cms_bp.route("/translations")
@login_required
def translations_page():
    root = current_app.root_path
    nl_path = f"{root}/translations/nl/LC_MESSAGES/messages.po"
    en_path = f"{root}/translations/en/LC_MESSAGES/messages.po"

    nl_entries = _parse_po(nl_path)
    en_entries = _parse_po(en_path)

    nl_index = {e["msgid"]: e for e in nl_entries if e["msgid"]}
    en_index = {e["msgid"]: e for e in en_entries if e["msgid"]}

    all_ids = sorted(set(list(nl_index.keys()) + list(en_index.keys())))

    nl_to_en = []
    en_to_nl = []

    for mid in all_ids:
        nl = nl_index.get(mid, {}).get("msgstr", "")
        en = en_index.get(mid, {}).get("msgstr", "")
        if nl:
            nl_to_en.append(
                {
                    "msgid": mid,
                    "source": nl,
                    "translation": en if en else mid,
                }
            )
        en_to_nl.append(
            {
                "msgid": mid,
                "source": mid,
                "translation": nl if nl else mid,
            }
        )

    review = _get_review()

    return render_template(
        "cms/translations.html",
        nl_to_en=nl_to_en,
        en_to_nl=en_to_nl,
        review=review,
    )


@cms_bp.route("/api/translations/review", methods=["POST"])
@login_required
def set_review():
    data = request.get_json(silent=True) or {}
    msgid = data.get("msgid", "").strip()
    status = data.get("status", "").strip()

    if not msgid:
        return api_error("msgid required", 400)
    if status not in ("flagged", "pending", "approved", ""):
        return api_error("invalid status", 400)

    review = _get_review()
    if status:
        review[msgid] = status
    else:
        review.pop(msgid, None)
    _save_review(review)
    return jsonify({"ok": True, "msgid": msgid, "status": status})


@cms_bp.route("/api/translations/flagged")
@login_required
def get_review_all():
    return jsonify(_get_review())


@cms_bp.route("/api/translations/manual-fix", methods=["POST"])
@login_required
def manual_fix():
    data = request.get_json(silent=True) or {}
    msgid = data.get("msgid", "").strip()
    direction = data.get("direction", "")
    translation = data.get("translation", "").strip()

    if not msgid or not direction or not translation:
        return api_error("msgid, direction en translation verplicht", 400)

    root_path = current_app.root_path
    if direction == "en→nl":
        path = os.path.join(root_path, "translations/nl/LC_MESSAGES/messages.po")
    elif direction == "nl→en":
        path = os.path.join(root_path, "translations/en/LC_MESSAGES/messages.po")
    else:
        return api_error("ongeldige richting", 400)

    _update_po_entry(path, msgid, translation)

    try:
        trans_dir = os.path.join(root_path, "translations")
        subprocess.run(
            ["pybabel", "compile", "-d", trans_dir],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        pass

    return jsonify({"ok": True})


@cms_bp.route("/api/translations/auto-fix", methods=["POST"])
@login_required
def auto_fix():
    from cms.services.ai_service import _generate

    data = request.get_json(silent=True) or {}
    en_to_nl_ids = data.get("en_to_nl", data.get("msgids", []))
    nl_to_en_ids = data.get("nl_to_en", [])

    if not isinstance(en_to_nl_ids, list):
        en_to_nl_ids = []
    if not isinstance(nl_to_en_ids, list):
        nl_to_en_ids = []

    if not en_to_nl_ids and not nl_to_en_ids:
        raw = Setting.get("translation_flags")
        logger.info("auto_fix: fallback to Setting = %r", raw)
        if raw:
            try:
                en_to_nl_ids = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("auto_fix: json parse failed: %s", e)

    if not en_to_nl_ids and not nl_to_en_ids:
        return api_error("Geen gemarkeerde vertalingen", 400)

    logger.info("auto_fix: en_to_nl=%r nl_to_en=%r", en_to_nl_ids, nl_to_en_ids)

    root_path = current_app.root_path
    nl_path = os.path.join(root_path, "translations/nl/LC_MESSAGES/messages.po")
    en_path = os.path.join(root_path, "translations/en/LC_MESSAGES/messages.po")
    trans_dir = os.path.join(root_path, "translations")

    nl_entries = _parse_po(nl_path)
    nl_index = {e["msgid"]: e["msgstr"] for e in nl_entries if e["msgid"]}

    results = []
    seen = set()

    for msgid in en_to_nl_ids:
        seen.add(msgid)
        prompt = (
            "Translate the following UI text from English to Dutch. "
            "Return ONLY the translation, no explanations or quotes:\n\n" + msgid
        )
        try:
            dutch = _generate(
                prompt,
                "You are a professional translator for an OSINT dashboard.",
                timeout=120,
            )
            if dutch and dutch.strip():
                _update_po_entry(nl_path, msgid, dutch.strip())
                results.append(
                    {"msgid": msgid, "status": "fixed", "direction": "en→nl"}
                )
            else:
                results.append(
                    {
                        "msgid": msgid,
                        "status": "error",
                        "error": "EN→NL: AI returned empty",
                    }
                )
        except Exception as e:
            results.append({"msgid": msgid, "status": "error", "error": f"EN→NL: {e}"})

    for msgid in nl_to_en_ids:
        if msgid in seen:
            continue
        nl_source = nl_index.get(msgid, "")
        if not nl_source or not nl_source.strip():
            results.append(
                {
                    "msgid": msgid,
                    "status": "error",
                    "error": "NL→EN: geen brontekst in NL .po",
                }
            )
            continue
        prompt = (
            "Translate the following UI text from Dutch to English. "
            "Return ONLY the translation, no explanations or quotes:\n\n" + nl_source
        )
        try:
            english = _generate(
                prompt,
                "You are a professional translator for an OSINT dashboard.",
                timeout=120,
            )
            if english and english.strip():
                _update_po_entry(en_path, msgid, english.strip())
                results.append(
                    {"msgid": msgid, "status": "fixed", "direction": "nl→en"}
                )
            else:
                results.append(
                    {
                        "msgid": msgid,
                        "status": "error",
                        "error": "NL→EN: AI returned empty",
                    }
                )
        except Exception as e:
            results.append({"msgid": msgid, "status": "error", "error": f"NL→EN: {e}"})

    try:
        subprocess.run(
            ["pybabel", "compile", "-d", trans_dir],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        pass

    fixed = [r["msgid"] for r in results if r["status"] == "fixed"]

    review = _get_review()
    for msgid in fixed:
        review[msgid] = "pending"
    _save_review(review)

    ai_error = get_openrouter_error()
    has_failures = len(results) - len(fixed) > 0
    if not fixed and has_failures and ai_error:
        # All failed due to OpenRouter — surface the real error
        pass

    return jsonify(
        {
            "results": results,
            "fixed": len(fixed),
            "errors": len(results) - len(fixed),
            "ai_error": ai_error if (not fixed and has_failures) else None,
        }
    )
