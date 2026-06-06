import json
import logging
import re

from flask import current_app, request, jsonify, render_template
from flask_login import login_required

from . import cms_bp
from .. import csrf
from ..models import Setting

logger = logging.getLogger(__name__)


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

    flagged_raw = Setting.get("translation_flags")
    flagged = set()
    if flagged_raw:
        try:
            flagged = set(json.loads(flagged_raw))
        except (json.JSONDecodeError, TypeError):
            flagged = set()

    return render_template(
        "cms/translations.html",
        nl_to_en=nl_to_en,
        en_to_nl=en_to_nl,
        flagged=flagged,
    )


@cms_bp.route("/api/translations/flag", methods=["POST"])
@csrf.exempt
@login_required
def flag_translation():
    data = request.get_json(silent=True) or {}
    msgid = data.get("msgid", "").strip()
    flagged = data.get("flagged", True)

    if not msgid:
        return jsonify({"error": "msgid required"}), 400

    flagged_raw = Setting.get("translation_flags")
    flags = set()
    if flagged_raw:
        try:
            flags = set(json.loads(flagged_raw))
        except (json.JSONDecodeError, TypeError):
            flags = set()

    if flagged:
        flags.add(msgid)
    else:
        flags.discard(msgid)

    Setting.set("translation_flags", json.dumps(list(flags)))
    return jsonify({"ok": True, "flagged": msgid in (flags if flagged else set())})


@cms_bp.route("/api/translations/flagged")
@login_required
def get_flagged():
    flagged_raw = Setting.get("translation_flags")
    flags = []
    if flagged_raw:
        try:
            flags = json.loads(flagged_raw)
        except (json.JSONDecodeError, TypeError):
            flags = []
    return jsonify({"flagged": flags})
