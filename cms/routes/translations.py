import json
import logging
import re

from flask import request, jsonify, render_template
from flask_login import login_required

from . import cms_bp
from .. import csrf
from ..models import Setting

logger = logging.getLogger(__name__)


def _parse_po(filepath):
    entries = []
    current = None
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("msgid "):
                if current:
                    entries.append(current)
                val = re.match(r'msgid "(.*)"', line)
                current = {
                    "msgid": val.group(1) if val else "",
                    "msgstr": "",
                    "line": None,
                }
            elif line.startswith("msgstr ") and current:
                val = re.match(r'msgstr "(.*)"', line)
                if val:
                    current["msgstr"] = val.group(1)
    if current:
        entries.append(current)
    return entries


@cms_bp.route("/translations")
@login_required
def translations_page():
    nl_path = "translations/nl/LC_MESSAGES/messages.po"
    en_path = "translations/en/LC_MESSAGES/messages.po"

    nl_entries = _parse_po(nl_path)
    en_entries = _parse_po(en_path)

    nl_index = {e["msgid"]: e for e in nl_entries if e["msgid"]}
    en_index = {e["msgid"]: e for e in en_entries if e["msgid"]}

    all_ids = sorted(set(list(nl_index.keys()) + list(en_index.keys())))

    nl_to_en = []
    en_to_nl = []

    for msgid in all_ids:
        nl = nl_index.get(msgid, {}).get("msgstr", "")
        en = en_index.get(msgid, {}).get("msgstr", "")
        if nl:
            nl_to_en.append(
                {
                    "source": nl,
                    "translation": en if en else msgid,
                }
            )
        en_to_nl.append(
            {
                "source": msgid,
                "translation": nl if nl else msgid,
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
