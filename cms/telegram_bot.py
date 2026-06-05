import asyncio
import logging
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

# ---------------------------------------------------------------------------
# helpers (called at startup, inside Flask app context)
# ---------------------------------------------------------------------------


def _ensure_api_key(app):
    """Create or retrieve a dedicated API key for the bot's internal use."""
    global _internal_api_key
    if _internal_api_key:
        return _internal_api_key
    from .models import db, ApiKey, User

    existing = ApiKey.query.filter_by(
        name="Telegram Bot Internal", is_active=True
    ).first()
    if existing:
        logger.info(
            "Found existing bot API key record (can't extract raw key) – generating fresh one"
        )
        db.session.delete(existing)
        db.session.commit()
    admin = User.query.filter_by(role="admin").first()
    raw, key_hash = ApiKey.generate_key()
    prefix = raw[:8]
    record = ApiKey(
        name="Telegram Bot Internal",
        key_hash=key_hash,
        key_prefix=prefix,
        user_id=admin.id if admin else User.query.first().id,
        scopes=["read", "write"],
        is_active=True,
    )
    db.session.add(record)
    db.session.commit()
    _internal_api_key = raw
    logger.info("Telegram bot internal API key created")
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
    if data.get("breaches"):
        lines.append(f"\n⚠️ *Breaches*: {', '.join(data['breaches'][:5])}")
    if data.get("social"):
        found = [s["name"] for s in data["social"] if s.get("exists")]
        if found:
            lines.append(f"\n🔗 *Social*: {', '.join(found[:10])}")
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
    for key, label in [
        ("ip", "IP"),
        ("city", "City"),
        ("region", "Region"),
        ("country_name", "Country"),
        ("org", "ISP"),
        ("asn", "ASN"),
        ("hostname", "Hostname"),
    ]:
        v = data.get(key)
        if v:
            lines.append(f"\n*{label}*: {v}")
    if data.get("proxy", False):
        lines.append("\n⚠️ *Proxy/VPN detected*")
    return "\n".join(lines)


def _fmt_domain(data: dict) -> str:
    lines = ["🌍 *Domain Lookup*"]
    for key, label in [
        ("domain", "Domain"),
        ("registrar", "Registrar"),
        ("creation_date", "Created"),
        ("expiration_date", "Expires"),
        ("organization", "Org"),
    ]:
        v = data.get(key)
        if v:
            lines.append(f"\n*{label}*: {v}")
    ns = data.get("name_servers")
    if ns:
        items = ns if isinstance(ns, list) else [ns]
        lines.append(f"\n*NS*: {' '.join(items[:4])}")
    mx = data.get("mx_records")
    if mx:
        items = mx if isinstance(mx, list) else [mx]
        lines.append(f"\n*MX*: {' '.join(items[:4])}")
    return "\n".join(lines)


def _fmt_person(data: dict) -> str:
    lines = ["👤 *Person Search*"]
    if data.get("error"):
        lines.append(f"\n❌ {data['error']}")
        return "\n".join(lines)
    lines.append(f"\n*Name*: {data.get('name', '?')}")
    lines.append(f"*Sources*: {', '.join(data.get('sources_used', ['none']))}")
    count = data.get("total_results", 0)
    lines.append(f"*Results*: {count}")
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
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:5000", timeout=timeout
    ) as c:
        r = await c.post(path, json=json_body, headers=headers)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text[:500]}


async def _api_get(path: str) -> tuple[int, dict]:
    headers = {"X-API-Key": _internal_api_key or ""}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:5000", timeout=10.0) as c:
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
    if not await _auth(update):
        return await update.message.reply_text(
            "⛔ Unauthorized. Your Telegram ID is not in the allowed users list."
        )
    name = update.effective_user.first_name or "User"
    await update.message.reply_text(
        f"👋 Hello *{name}*!\n\n"
        "I'm your OSINT Dashboard bot. Use /help to see available commands.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    if not await _auth(update):
        return await update.message.reply_text("⛔ Unauthorized.")
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
    if not await _auth(update):
        return await update.message.reply_text("⛔ Unauthorized.")
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
    if not await _auth(update):
        return await update.message.reply_text("⛔ Unauthorized.")
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
    if not await _auth(update):
        return await update.message.reply_text("⛔ Unauthorized.")
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
    if not await _auth(update):
        return await update.message.reply_text("⛔ Unauthorized.")
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
    if not await _auth(update):
        return await update.message.reply_text("⛔ Unauthorized.")
    if not context.args:
        return await update.message.reply_text(
            "Usage: `/person Jan Jansen`", parse_mode="Markdown"
        )
    name = " ".join(context.args)
    msg = await update.message.reply_text(
        f"🔍 Searching for `{name}`...", parse_mode="Markdown"
    )
    status, data = await _api_post("/api/person", {"name": name}, timeout=60.0)
    if status == 200:
        await msg.edit_text(_fmt_person(data), parse_mode="Markdown")
    else:
        await msg.edit_text(
            f"❌ *Error*: {data.get('error', data.get('raw', status))}",
            parse_mode="Markdown",
        )


async def cmd_status(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    if not await _auth(update):
        return await update.message.reply_text("⛔ Unauthorized.")
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


def run_bot_polling(token: str):
    """Run the bot's event loop in the calling thread (blocking).

    Uses manual start/stop lifecycle instead of ``run_polling()`` to avoid
    ``set_wakeup_fd`` errors when signal handlers are registered
    in a non-main thread.
    """
    global _bot_app
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

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
    try:
        loop.run_until_complete(app.initialize())
        loop.run_until_complete(
            app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
        )
        loop.run_until_complete(app.start())
        loop.run_forever()
    except asyncio.CancelledError:
        logger.info("Telegram bot: polling cancelled")
    except Exception as exc:
        logger.error("Telegram bot polling failed: %s", exc, exc_info=True)
    finally:
        try:
            loop.run_until_complete(app.shutdown())
        except Exception:
            pass
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
