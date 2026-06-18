import logging
import os
import threading
from typing import Optional

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

_bot_app: Optional[Application] = None
_bot_thread: Optional[threading.Thread] = None
_internal_api_key: Optional[str] = None
# cached config – read once at startup (inside app context), then used from bot thread
_cached_allowed_users: str = ""

_INTERNAL_PORT = os.environ.get("PORT", "5000")
_INTERNAL_BASE = f"http://127.0.0.1:{_INTERNAL_PORT}"

# ---------------------------------------------------------------------------
# helpers (called at startup, inside Flask app context)
# ---------------------------------------------------------------------------


def _ensure_api_key(app):
    """Create or retrieve a dedicated API key for the bot's internal use.

    Uses a deterministic key derived from SECRET_KEY so all gunicorn
    workers produce the same key (no race between worker processes).
    """
    global _internal_api_key
    if _internal_api_key:
        return _internal_api_key
    import hashlib
    from werkzeug.security import generate_password_hash

    from .models import db, ApiKey, User

    secret = app.config.get("SECRET_KEY", "dev-fallback-key")
    raw = (
        "osint_tg_"
        + hashlib.sha256((secret + ":telegram-bot-internal").encode()).hexdigest()[:32]
    )

    logger.info(
        "_ensure_api_key: computed deterministic key prefix=%s len=%d",
        raw[:8],
        len(raw),
    )

    existing = ApiKey.query.filter_by(
        name="Telegram Bot Internal", is_active=True
    ).first()
    if existing:
        if existing.verify_key(raw):
            logger.info("Reusing existing bot API key (deterministic match)")
            _internal_api_key = raw
            return raw
        logger.info(
            "Bot API key hash mismatch for name=%s – updating existing record",
            existing.name,
        )
        existing.key_hash = generate_password_hash(raw, method="pbkdf2:sha256")
        existing.key_prefix = raw[:8]
        db.session.commit()
        _internal_api_key = raw
        return raw

    admin = User.query.filter_by(role="admin").first()
    key_hash = generate_password_hash(raw, method="pbkdf2:sha256")
    record = ApiKey(
        name="Telegram Bot Internal",
        key_hash=key_hash,
        key_prefix=raw[:8],
        user_id=admin.id if admin else User.query.first().id,
        scopes=["read", "write"],
        is_active=True,
    )
    db.session.add(record)
    db.session.commit()
    _internal_api_key = raw
    logger.info("Telegram bot internal API key created (new record)")
    return raw


def _check_enabled() -> bool:
    from .models import Setting

    return Setting.get("telegram_enabled", "false").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# auth (no DB access – uses cached config)
# ---------------------------------------------------------------------------


def _is_user_allowed(user_id: int, username: str = "") -> bool:
    allowed = _cached_allowed_users
    if not allowed:
        logger.warning("telegram_allowed_users is empty – all users rejected")
        return False
    parts = [u.strip().lstrip("@") for u in allowed.split(",") if u.strip()]
    if str(user_id) in parts:
        return True
    if username and username.lower() in [p.lower() for p in parts]:
        return True
    logger.info(
        "Auth rejected: user_id=%s username=%s allowed=%s", user_id, username, parts
    )
    return False


async def _auth(update: Update) -> bool:
    if not update.effective_user:
        return False
    return _is_user_allowed(
        update.effective_user.id, update.effective_user.username or ""
    )


# ---------------------------------------------------------------------------
# formatters
# ---------------------------------------------------------------------------


def _fmt_email(data: dict) -> str:
    lines = ["📧 *Email Lookup*"]
    email = data.get("email", "")
    if email:
        lines.append(f"\n📧 `{email}`")
    if not data.get("valid_format", True):
        lines.append("\n❌ Invalid email format")
        return "\n".join(lines)
    provider = data.get("provider", "")
    if provider:
        lines.append(f"\n🏢 *Provider*: {provider}")
    if data.get("disposable"):
        lines.append("\n⚠️ *Disposable email*")
    mx = data.get("mx_records")
    if mx:
        lines.append(f"\n📡 *MX*: {' '.join(mx[:3])}")
    checks = data.get("account_checks", [])
    found = [c for c in checks if c.get("exists") == True]
    if found:
        sites = [c.get("site", "?") for c in found[:10]]
        lines.append(f"\n🔗 *Accounts found* ({len(found)}): {', '.join(sites)}")
    links = data.get("search_links", [])
    if links:
        names = [link.get("name", "?") for link in links[:5]]
        lines.append(f"\n🔍 *Manual checks*: {', '.join(names)}")
    if not lines[1:]:
        lines.append("\nℹ️ No significant findings.")
    return "\n".join(lines)


def _fmt_phone(data: dict) -> str:
    lines = ["📞 *Phone Lookup*"]
    if data.get("valid"):
        lines.append(f"\n📱 *{data.get('formatted', '?')}*")
        if data.get("country"):
            lines.append(f"🌍 *Country*: {data['country']}")
        if data.get("carrier"):
            lines.append(f"🏢 *Carrier*: {data['carrier']}")
        if data.get("line_type"):
            lines.append(f"📋 *Type*: {data['line_type']}")
        if data.get("timezone"):
            lines.append(f"🕐 *Timezone*: {data['timezone']}")
        svc = data.get("services", {})
        wa = svc.get("whatsapp", {})
        tg = svc.get("telegram", {})
        lines.append(f"\n{'✅' if wa.get('exists') else '❌'} WhatsApp")
        lines.append(f"{'✅' if tg.get('exists') else '❌'} Telegram")
    else:
        lines.append("\n❌ Invalid phone number.")
    return "\n".join(lines)


def _fmt_ip(data: dict) -> str:
    lines = ["🌐 *IP Lookup*"]
    ip = data.get("ip", "")
    if ip:
        lines.append(f"\n🌐 `{ip}`")
    if not data.get("valid", True):
        lines.append("\n❌ Invalid IP address")
        return "\n".join(lines)
    rdns = data.get("reverse_dns")
    if rdns and rdns != "N/A":
        lines.append(f"\n📡 *RDNS*: {rdns}")
    geo = data.get("geolocation")
    if geo:
        for label, key in [
            ("Country", "country"),
            ("Region", "region"),
            ("City", "city"),
            ("ISP", "isp"),
            ("Org", "org"),
        ]:
            v = geo.get(key)
            if v and v != "N/A":
                lines.append(f"\n*{label}*: {v}")
    ipapi = data.get("ipapi")
    if ipapi:
        if not geo:
            for label, key in [
                ("Country", "country_name"),
                ("Region", "region"),
                ("City", "city"),
            ]:
                v = ipapi.get(key)
                if v and v != "N/A":
                    lines.append(f"\n*{label}*: {v}")
        asn = ipapi.get("asn")
        if asn and asn != "N/A":
            lines.append(f"\n*ASN*: {asn}")
        org = ipapi.get("org")
        if org and org != "N/A" and not (geo and geo.get("org")):
            lines.append(f"\n*Org*: {org}")
    ports = data.get("ports", [])
    if ports:
        lines.append(f"\n🔌 *Open ports*: {', '.join(str(p) for p in ports[:10])}")
    score = data.get("reputation_score", 0)
    icon = "✅" if score >= 80 else "⚠️" if score >= 50 else "❌"
    lines.append(f"\n{icon} *Reputation*: {score}/100")
    return "\n".join(lines)


def _fmt_domain(data: dict) -> str:
    lines = ["🌍 *Domain Lookup*"]
    domain = data.get("domain", "")
    if domain:
        lines.append(f"\n🌍 `{domain}`")
    if not data.get("valid", True):
        lines.append("\n❌ Invalid domain")
        return "\n".join(lines)
    ips = data.get("ip_addresses", [])
    if ips:
        lines.append(f"\n📡 *A*: {', '.join(ips[:3])}")
    whois = data.get("whois", {})
    if whois.get("registrar"):
        lines.append(f"\n🏢 *Registrar*: {whois['registrar']}")
    if whois.get("registration_date"):
        lines.append(f"\n📅 *Created*: {whois['registration_date']}")
    if whois.get("expiration_date"):
        lines.append(f"\n⏳ *Expires*: {whois['expiration_date']}")
    ns = whois.get("name_servers", [])
    if ns:
        lines.append(f"\n*NS*: {' '.join(ns[:4])}")
    dns = data.get("dns_records", {})
    mx = dns.get("MX")
    if mx:
        hosts = [m.get("host", str(m)) for m in (mx if isinstance(mx, list) else [mx])]
        lines.append(f"\n*MX*: {' '.join(hosts[:4])}")
    txt = dns.get("TXT")
    if txt:
        items = txt if isinstance(txt, list) else [txt]
        lines.append(f"\n*TXT*: {' '.join(items[:2])}")
    subs = data.get("subdomains", [])
    if subs:
        lines.append(f"\n🔗 *Subdomains*: {', '.join(subs[:6])}")
    ssl = data.get("ssl_info")
    if ssl and isinstance(ssl, dict) and ssl.get("issuer"):
        org = ssl["issuer"].get("organizationName", "")
        if org:
            lines.append(f"\n🔒 *SSL issuer*: {org}")
    return "\n".join(lines)


def _fmt_person(data: dict) -> str:
    lines = ["👤 *Person Search*"]
    if data.get("error"):
        lines.append(f"\n❌ {data['error']}")
        return "\n".join(lines)
    lines.append(f"\n*Name*: {data.get('name', '?')}")
    sources = data.get("sources_used", [])
    count = data.get("total_results", 0)
    lines.append(f"*Results*: {count}")
    brave_err = data.get("brave_error", "")
    if brave_err:
        lines.append(f"⚠️ *Brave*: {brave_err}")
    if sources:
        lines.append(f"*Sources*: {', '.join(sources)}")
    if count:
        categories = {}
        for r in data.get("dorks_results", []):
            cat = r.get("category", "general")
            categories.setdefault(cat, [])
            categories[cat].append(r.get("url", ""))
        for cat, urls in list(categories.items())[:5]:
            lines.append(f"\n📂 *{cat.replace('_', ' ').title()}*")
            for u in urls[:3]:
                lines.append(f"  `{u[:60]}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTTP helpers  (async – called from handlers)
# ---------------------------------------------------------------------------


async def _api_post(
    path: str, json_body: dict, timeout: float = 25.0
) -> tuple[int, dict]:
    headers = {
        "X-API-Key": _internal_api_key or "",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(base_url=_INTERNAL_BASE, timeout=timeout) as c:
        r = await c.post(path, json=json_body, headers=headers)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text[:500]}


async def _api_get(path: str) -> tuple[int, dict]:
    headers = {"X-API-Key": _internal_api_key or ""}
    async with httpx.AsyncClient(base_url=_INTERNAL_BASE, timeout=10.0) as c:
        r = await c.get(path, headers=headers)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text[:500]}


# ---------------------------------------------------------------------------
# ptb error handler
# ---------------------------------------------------------------------------


async def _error_handler(update: Optional[Update], context: ContextTypes.DEFAULT_TYPE):
    logger.error("Bot handler error: %s", context.error, exc_info=context.error)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"❌ Internal error: {type(context.error).__name__}: {context.error}"
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not await _auth(update):
        return
    name = update.effective_user.first_name or "User"
    await update.message.reply_text(
        f"👋 Hello *{name}*!\n\n"
        "I'm your OSINT Dashboard bot. Use /help to see available commands.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not await _auth(update):
        return
    await update.message.reply_text(
        "🔍 *OSINT Bot Commands*\n\n"
        "`/email <address>` — Email breach & social lookup\n"
        "`/phone <number>` — Phone enrichment (WhatsApp/Telegram)\n"
        "`/ip <address>` — IP geolocation\n"
        "`/domain <domain>` — Domain WHOIS/DNS\n"
        "`/person <name>` — Person search (web + social)\n"
        "`/status` — Dashboard & bot health\n"
        "`/help` — This message",
        parse_mode="Markdown",
    )


async def cmd_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not await _auth(update):
        return
    if not context.args:
        return await update.message.reply_text(
            "Usage: `/email user@example.com`", parse_mode="Markdown"
        )
    email = context.args[0]
    msg = await update.message.reply_text(
        f"🔍 Looking up `{email}`...", parse_mode="Markdown"
    )
    status, data = await _api_post("/api/email", {"email": email})
    if status == 200:
        await msg.edit_text(_fmt_email(data), parse_mode="Markdown")
    else:
        await msg.edit_text(
            f"❌ *Error*: {data.get('error', data.get('raw', status))}",
            parse_mode="Markdown",
        )


async def cmd_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not await _auth(update):
        return
    if not context.args:
        return await update.message.reply_text(
            "Usage: `/phone +31612345678`", parse_mode="Markdown"
        )
    number = context.args[0]
    msg = await update.message.reply_text(
        f"🔍 Looking up `{number}`...", parse_mode="Markdown"
    )
    status, data = await _api_post("/cms/api/phone-lookup", {"phone": number})
    if status == 200:
        await msg.edit_text(_fmt_phone(data), parse_mode="Markdown")
    else:
        await msg.edit_text(
            f"❌ *Error*: {data.get('error', data.get('raw', status))}",
            parse_mode="Markdown",
        )


async def cmd_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not await _auth(update):
        return
    if not context.args:
        return await update.message.reply_text(
            "Usage: `/ip 8.8.8.8`", parse_mode="Markdown"
        )
    ip = context.args[0]
    msg = await update.message.reply_text(
        f"🔍 Looking up `{ip}`...", parse_mode="Markdown"
    )
    status, data = await _api_post("/api/ip", {"ip": ip})
    if status == 200:
        await msg.edit_text(_fmt_ip(data), parse_mode="Markdown")
    else:
        await msg.edit_text(
            f"❌ *Error*: {data.get('error', data.get('raw', status))}",
            parse_mode="Markdown",
        )


async def cmd_domain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not await _auth(update):
        return
    if not context.args:
        return await update.message.reply_text(
            "Usage: `/domain example.com`", parse_mode="Markdown"
        )
    domain = context.args[0]
    msg = await update.message.reply_text(
        f"🔍 Looking up `{domain}`...", parse_mode="Markdown"
    )
    status, data = await _api_post("/api/domain", {"domain": domain})
    if status == 200:
        await msg.edit_text(_fmt_domain(data), parse_mode="Markdown")
    else:
        await msg.edit_text(
            f"❌ *Error*: {data.get('error', data.get('raw', status))}",
            parse_mode="Markdown",
        )


async def cmd_person(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not await _auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/person Jan Jansen`", parse_mode="Markdown"
        )
        return
    name = " ".join(context.args)
    msg = await update.message.reply_text(
        f"🔍 Searching for `{name}`...", parse_mode="Markdown"
    )
    status, data = await _api_post("/api/person", {"name": name}, timeout=120.0)
    if status == 200:
        await msg.edit_text(_fmt_person(data), parse_mode="Markdown")
    else:
        err = data.get("error", data.get("raw", str(status)))
        await msg.edit_text(
            f"❌ *Error*: {err}",
            parse_mode="Markdown",
        )


async def cmd_status(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not await _auth(update):
        return
    status, data = await _api_get("/health?quick=1")
    if status == 200:
        await update.message.reply_text(
            f"📊 *Dashboard Status*\n\n"
            f"Database: {data.get('database', '?')}\n"
            f"SpiderFoot: {data.get('spiderfoot', '?')}\n"
            f"Tor: {data.get('tor', '?')}\n"
            f"📡 Bot: ✅ Online",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(f"❌ Dashboard returned HTTP {status}")


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


_BOT_START_DELAY = 5  # seconds to wait between retrying on Conflict


def _await_clear(token: str) -> None:
    """Block until Telegram no longer returns Conflict for this token.

    ``getUpdates`` long-poll sessions can linger on Telegram's side for
    up to ~60 s after the previous polling process stops.  This helper
    calls ``getUpdates`` with a 1 s timeout and retries when Telegram
    returns error code 409.
    """
    import time as _time

    retries = 12  # up to ~1 min with 5 s gaps

    for attempt in range(retries):
        try:
            resp = httpx.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"timeout": 1, "limit": 1},
                timeout=10,
            )
            data = resp.json()
            if data.get("ok", False):
                return
            if data.get("error_code") != 409:
                return  # unexpected error — let PTB handle it
        except httpx.RequestError:
            return  # network glitch — let PTB handle it

        logger.warning(
            "Telegram Conflict (await-clear attempt %d/%d), waiting %ds …",
            attempt + 1,
            retries,
            _BOT_START_DELAY,
        )
        _time.sleep(_BOT_START_DELAY)

    raise RuntimeError("Telegram session did not clear after 12 attempts")


def run_bot_polling(token: str):
    """Run the bot via PTB's ``Application.run_polling()`` (blocking).

    Since the bot is now a standalone systemd service (main thread), the
    original workaround for ``set_wakeup_fd`` in non-main threads is no
    longer needed.
    """
    global _bot_app

    _await_clear(token)

    app = Application.builder().token(token).build()
    _bot_app = app

    app.add_error_handler(_error_handler)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("email", cmd_email))
    app.add_handler(CommandHandler("phone", cmd_phone))
    app.add_handler(CommandHandler("ip", cmd_ip))
    app.add_handler(CommandHandler("domain", cmd_domain))
    app.add_handler(CommandHandler("person", cmd_person))
    app.add_handler(CommandHandler("status", cmd_status))

    logger.info("Telegram bot: starting polling ...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )
    _bot_app = None
    logger.info("Telegram bot: polling stopped")


def start_bot(app):
    global _bot_thread, _internal_api_key, _cached_allowed_users
    if _bot_thread and _bot_thread.is_alive():
        logger.info("Telegram bot already running")
        return
    if not _check_enabled():
        logger.info("Telegram bot not enabled (telegram_enabled != true)")
        return
    from .models import Setting

    token = Setting.get("telegram_bot_token", "")
    if not token:
        logger.warning(
            "Telegram bot not started: no token (set telegram_bot_token Setting)"
        )
        return
    _cached_allowed_users = Setting.get("telegram_allowed_users", "") or ""
    _internal_api_key = _ensure_api_key(app)
    _bot_thread = threading.Thread(
        target=run_bot_polling,
        args=(token,),
        name="telegram-bot",
        daemon=True,
    )
    _bot_thread.start()
    logger.info("Telegram bot thread started")
