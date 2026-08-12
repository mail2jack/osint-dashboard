"""Stripe billing integration — checkout, portal, webhooks with dunning and trial management."""

import logging
import json
from datetime import datetime, timezone, timedelta

import stripe
from flask import (
    Blueprint,
    redirect,
    request,
    jsonify,
    current_app,
    url_for,
    flash,
    abort,
)
from flask_login import login_required, current_user

from ..models import db, Tenant, Notification, ProrationLog
from ..auth import admin_required
from .. import csrf
from . import cms_bp

logger = logging.getLogger(__name__)

stripe_bp = Blueprint("stripe", __name__, url_prefix="/stripe")


def _price_to_tier() -> dict[str, str]:
    """Return mapping of Stripe Price ID to tier name."""
    from ..models import PlatformSetting

    raw = PlatformSetting.get("stripe_price_mapping")
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid stripe_price_mapping in PlatformSetting")
    return {}


def _tier_to_price(tier: str) -> str | None:
    mapping = _price_to_tier()
    for price_id, t in mapping.items():
        if t == tier:
            return price_id
    return None


def _get_stripe():
    key = current_app.config.get("STRIPE_SECRET_KEY", "")
    if not key:
        return None
    stripe.api_key = key
    return stripe


def _get_dunning_config() -> dict:
    """Return dunning configuration from PlatformSetting."""
    from ..models import PlatformSetting

    default = {"max_retries": 3, "downgrade_after_days": 7, "notify_after_days": 1}
    raw = PlatformSetting.get("dunning_config")
    if raw:
        try:
            return {**default, **json.loads(raw)}
        except (json.JSONDecodeError, TypeError):
            pass
    return default


def _send_billing_notification(
    tenant_id: str,
    user_id: str,
    category: str,
    title: str,
    message: str,
    link: str = "",
):
    """Create an in-app notification for billing events."""
    n = Notification(
        tenant_id=tenant_id,
        user_id=user_id,
        category=category,
        title=title,
        message=message,
        link=link,
    )
    db.session.add(n)


def _notify_tenant_admins(tenant, category, title, message, link=""):
    """Send notification to all active admins/owners of a tenant."""
    from ..models import User

    admins = User.query.filter(
        User.tenant_id == tenant.id,
        User.is_active == True,
        User.role.in_(["admin", "owner"]),
    ).all()
    for admin in admins:
        _send_billing_notification(tenant.id, admin.id, category, title, message, link)

    # Send SMS/WhatsApp for critical billing alerts
    from ..notifications import send_alert_sms_whatsapp

    send_alert_sms_whatsapp(tenant, category, title, message)


def _send_billing_email(tenant, subject, body_html, body_text=""):
    """Send billing email to tenant admins if SMTP is configured."""
    from ..models import User
    from ..email_utils import is_smtp_configured, send_email

    if not is_smtp_configured():
        return

    admins = User.query.filter(
        User.tenant_id == tenant.id,
        User.is_active == True,
        User.role.in_(["admin", "owner"]),
        User.email.isnot(None),
    ).all()
    seen = set()
    for admin in admins:
        if admin.email and admin.email not in seen:
            seen.add(admin.email)
            try:
                send_email(admin.email, subject, body_html, body_text)
            except Exception:
                logger.exception("Failed to send billing email to %s", admin.email)


# ---------------------------------------------------------------------------
# User-facing routes
# ---------------------------------------------------------------------------


@cms_bp.route("/settings/create-checkout-session", methods=["POST"])
@login_required
@admin_required
def create_checkout_session():
    tier = request.form.get("tier", "").strip().lower()
    if not tier:
        flash("No tier specified.", "danger")
        return redirect(url_for("cms.settings", category="plan"))

    from ..tier_limits import TIERS, TIER_DISPLAY

    if tier not in TIERS:
        flash(f"Unknown tier: {tier}", "danger")
        return redirect(url_for("cms.settings", category="plan"))

    stripe_mod = _get_stripe()
    if not stripe_mod:
        flash("Stripe is not configured. Contact the system administrator.", "danger")
        return redirect(url_for("cms.settings", category="plan"))

    price_id = _tier_to_price(tier)
    if not price_id:
        flash(
            f"No Stripe price configured for {TIER_DISPLAY.get(tier, tier.title())}. Contact the system administrator.",
            "danger",
        )
        return redirect(url_for("cms.settings", category="plan"))

    tenant = current_user.tenant
    success_url = (
        url_for("cms.settings", category="plan", _external=True)
        + "?session_id={CHECKOUT_SESSION_ID}"
    )
    cancel_url = url_for("cms.settings", category="plan", _external=True)

    try:
        params = {
            "mode": "subscription",
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "client_reference_id": tenant.id,
            "customer_email": current_user.email,
            "metadata": {"tenant_id": tenant.id, "tier": tier},
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if not tenant.stripe_customer_id:
            params["customer_creation"] = "always"
        session = stripe_mod.checkout.Session.create(**params)
        return redirect(session.url, code=303)
    except stripe.error.StripeError as e:
        logger.error("Stripe checkout error: %s", e)
        flash(f"Payment error: {e.user_message or 'Please try again.'}", "danger")
        return redirect(url_for("cms.settings", category="plan"))


@cms_bp.route("/settings/billing-portal", methods=["GET"])
@login_required
@admin_required
def billing_portal():
    stripe_mod = _get_stripe()
    if not stripe_mod:
        flash("Stripe is not configured.", "danger")
        return redirect(url_for("cms.settings", category="plan"))

    tenant = current_user.tenant
    if not tenant.stripe_customer_id:
        flash("No billing customer found. Subscribe to a plan first.", "warning")
        return redirect(url_for("cms.settings", category="plan"))

    try:
        session = stripe_mod.billing_portal.Session.create(
            customer=tenant.stripe_customer_id,
            return_url=url_for("cms.settings", category="plan", _external=True),
        )
        return redirect(session.url, code=303)
    except stripe.error.StripeError as e:
        logger.error("Stripe portal error: %s", e)
        flash(
            f"Billing portal error: {e.user_message or 'Please try again.'}", "danger"
        )
        return redirect(url_for("cms.settings", category="plan"))


# ---------------------------------------------------------------------------
# Stripe webhook
# ---------------------------------------------------------------------------


@stripe_bp.route("/webhook", methods=["POST"])
@csrf.exempt
def webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature", "")

    stripe_mod = _get_stripe()
    if not stripe_mod:
        logger.error("Stripe not configured but webhook received")
        return jsonify({"error": "Stripe not configured"}), 500

    webhook_secret = current_app.config.get("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET not set")
        return jsonify({"error": "Webhook secret not configured"}), 500

    try:
        event = stripe_mod.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        logger.warning("Stripe webhook: invalid payload")
        return jsonify({"error": "Invalid payload"}), 400
    except stripe_mod.error.SignatureVerificationError:
        logger.warning("Stripe webhook: invalid signature")
        return jsonify({"error": "Invalid signature"}), 400

    event_type = event.get("type")
    data = event.get("data", {}).get("object", {})

    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(data)
        elif event_type == "customer.subscription.updated":
            _handle_subscription_updated(data)
        elif event_type == "customer.subscription.deleted":
            _handle_subscription_deleted(data)
        elif event_type == "invoice.payment_succeeded":
            _handle_invoice_payment_succeeded(data)
        elif event_type == "invoice.payment_failed":
            _handle_invoice_payment_failed(data)
        elif event_type == "customer.subscription.trial_will_end":
            _handle_trial_will_end(data)
        else:
            logger.info("Stripe webhook: unhandled event %s", event_type)
    except Exception as e:
        logger.exception("Stripe webhook handler error for %s: %s", event_type, e)
        return jsonify({"error": "Internal error"}), 500

    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Webhook handlers
# ---------------------------------------------------------------------------


def _find_tenant_by_subscription(subscription_id: str) -> Tenant | None:
    return Tenant.query.filter_by(stripe_subscription_id=subscription_id).first()


def _find_tenant_by_customer(customer_id: str) -> Tenant | None:
    return Tenant.query.filter_by(stripe_customer_id=customer_id).first()


def _get_subscription_id_from_invoice(invoice: dict) -> str | None:
    return invoice.get("subscription") or invoice.get("subscription_details", {}).get(
        "id", ""
    )


def _handle_checkout_completed(session: dict):
    tenant_id = session.get("client_reference_id") or session.get("metadata", {}).get(
        "tenant_id"
    )
    if not tenant_id:
        logger.warning("checkout.session.completed: no tenant_id")
        return

    tenant = Tenant.query.get(tenant_id)
    if not tenant:
        logger.warning("checkout.session.completed: tenant %s not found", tenant_id)
        return

    customer_id = session.get("customer")
    subscription_id = session.get("subscription")
    tier = session.get("metadata", {}).get("tier", "professional")

    tenant.stripe_customer_id = customer_id
    tenant.stripe_subscription_id = subscription_id
    tenant.subscription_status = "active"
    tenant.tier = tier
    tenant.dunning_retries = 0
    db.session.commit()

    _notify_tenant_admins(
        tenant,
        "system",
        "Subscription activated",
        f"Your {tier} subscription is now active. Welcome aboard!",
        url_for("cms.settings", category="plan", _external=True),
    )

    logger.info(
        "Tenant %s subscribed to %s (customer=%s, sub=%s)",
        tenant_id,
        tier,
        customer_id,
        subscription_id,
    )


def _handle_subscription_updated(subscription: dict):
    sub_id = subscription.get("id")
    status = subscription.get("status", "incomplete")
    metadata = subscription.get("metadata", {})
    items = subscription.get("items", {}).get("data", [])

    tenant = _find_tenant_by_subscription(sub_id)
    if not tenant:
        tenant_id = metadata.get("tenant_id")
        if tenant_id:
            tenant = Tenant.query.get(tenant_id)
    if not tenant:
        logger.warning("subscription.updated: no tenant for sub %s", sub_id)
        return

    current_period_end = subscription.get("current_period_end")
    if current_period_end:
        tenant.current_period_end = datetime.fromtimestamp(
            current_period_end, tz=timezone.utc
        )

    trial_end = subscription.get("trial_end")
    if trial_end:
        tenant.trial_ends_at = datetime.fromtimestamp(trial_end, tz=timezone.utc)

    if items:
        price_id = items[0].get("price", {}).get("id", "")
        tier = _price_to_tier().get(price_id)
        if tier:
            tenant.tier = tier

    tenant.subscription_status = status
    if status == "active":
        tenant.dunning_retries = 0
        # Clear retention fields if tenant resubscribes after cancellation
        if tenant.scheduled_deletion_at:
            tenant.canceled_at = None
            tenant.scheduled_deletion_at = None

    db.session.commit()
    logger.info("Tenant %s subscription %s -> %s", tenant.id, sub_id, status)


def _handle_subscription_deleted(subscription: dict):
    sub_id = subscription.get("id")
    tenant = _find_tenant_by_subscription(sub_id)
    if not tenant:
        logger.warning("subscription.deleted: no tenant for sub %s", sub_id)
        return

    now_utc = datetime.now(timezone.utc)
    tenant.subscription_status = "canceled"
    tenant.tier = "free"
    tenant.stripe_subscription_id = None
    tenant.current_period_end = None
    tenant.canceled_at = now_utc
    tenant.scheduled_deletion_at = now_utc + timedelta(days=30)
    db.session.commit()

    _notify_tenant_admins(
        tenant,
        "system",
        "Subscription canceled",
        "Your subscription has been canceled. Your data will be retained for 30 days. Contact support to reactivate.",
        url_for("cms.settings", category="plan", _external=True),
    )

    logger.info("Tenant %s subscription %s canceled -> free", tenant.id, sub_id)


def _handle_invoice_payment_succeeded(invoice: dict):
    """Handle successful payment — reset dunning state, notify admins."""
    customer_id = invoice.get("customer")
    sub_id = _get_subscription_id_from_invoice(invoice)

    tenant = None
    if sub_id:
        tenant = _find_tenant_by_subscription(sub_id)
    if not tenant and customer_id:
        tenant = _find_tenant_by_customer(customer_id)
    if not tenant:
        logger.warning(
            "invoice.payment_succeeded: no tenant for customer %s", customer_id
        )
        return

    was_in_dunning = tenant.dunning_retries > 0
    tenant.dunning_retries = 0

    amount = invoice.get("total", 0) / 100
    currency = (invoice.get("currency") or "eur").upper()

    if was_in_dunning:
        _notify_tenant_admins(
            tenant,
            "system",
            "Payment successful",
            f"A payment of {currency} {amount:.2f} was successfully processed. Your subscription is up to date.",
            url_for("cms.settings", category="plan", _external=True),
        )

    db.session.commit()
    logger.info("Tenant %s payment succeeded: %s %.2f", tenant.id, currency, amount)


def _handle_invoice_payment_failed(invoice: dict):
    """Handle failed payment — increment dunning retries, notify, auto-downgrade at threshold."""
    customer_id = invoice.get("customer")
    sub_id = _get_subscription_id_from_invoice(invoice)
    attempt_count = invoice.get("attempt_count", 1)

    tenant = None
    if sub_id:
        tenant = _find_tenant_by_subscription(sub_id)
    if not tenant and customer_id:
        tenant = _find_tenant_by_customer(customer_id)
    if not tenant:
        logger.warning("invoice.payment_failed: no tenant for customer %s", customer_id)
        return

    config = _get_dunning_config()
    tenant.dunning_retries = attempt_count

    amount = invoice.get("total", 0) / 100
    currency = (invoice.get("currency") or "eur").upper()

    # Notify tenant admins
    _notify_tenant_admins(
        tenant,
        "usage_alerts",
        "Payment failed",
        f"Payment of {currency} {amount:.2f} failed (attempt {attempt_count}/{config['max_retries'] + 1}). "
        f"Update your payment method to avoid service interruption.",
        url_for("cms.settings", category="plan", _external=True),
    )

    # Send email to all tenant admins
    _send_billing_email(
        tenant,
        f"[URGENT] Payment failed for {tenant.name}",
        f"<p>Payment of {currency} {amount:.2f} failed (attempt {attempt_count}/{config['max_retries'] + 1}).</p>"
        f"<p>Update your payment method here: "
        f"<a href='{url_for('cms.settings', category='plan', _external=True)}'>Billing Settings</a></p>",
        f"Payment of {currency} {amount:.2f} failed (attempt {attempt_count}/{config['max_retries'] + 1}).\n"
        f"Update your payment method: {url_for('cms.settings', category='plan', _external=True)}",
    )

    # Auto-downgrade after max retries
    if attempt_count > config["max_retries"]:
        old_tier = tenant.tier
        tenant.tier = "free"
        tenant.subscription_status = "past_due"
        db.session.commit()

        _notify_tenant_admins(
            tenant,
            "usage_alerts",
            "Subscription downgraded",
            f"After {attempt_count} failed payment attempts, your plan has been downgraded from {old_tier} to Free. "
            f"Reactivate your subscription to restore access to higher-tier features.",
            url_for("cms.settings", category="plan", _external=True),
        )

        logger.warning(
            "Tenant %s auto-downgraded from %s to free after %d failed payments",
            tenant.id,
            old_tier,
            attempt_count,
        )
    else:
        db.session.commit()

    logger.info(
        "Tenant %s payment failed: %s %.2f (attempt %d)",
        tenant.id,
        currency,
        amount,
        attempt_count,
    )


def _handle_trial_will_end(data: dict):
    """Send notification when trial is about to end."""
    sub_id = data.get("id")
    trial_end_ts = data.get("trial_end")
    tenant = _find_tenant_by_subscription(sub_id)
    if not tenant:
        logger.warning("trial_will_end: no tenant for sub %s", sub_id)
        return

    if trial_end_ts:
        trial_end_dt = datetime.fromtimestamp(trial_end_ts, tz=timezone.utc)
        tenant.trial_ends_at = trial_end_dt
        db.session.commit()

    days_left = (
        (tenant.trial_ends_at - datetime.now(timezone.utc)).days
        if tenant.trial_ends_at
        else 0
    )

    _notify_tenant_admins(
        tenant,
        "usage_alerts",
        "Trial ending soon",
        f"Your trial ends in {days_left} day(s). Add a payment method to keep your subscription active.",
        url_for("cms.settings", category="plan", _external=True),
    )

    _send_billing_email(
        tenant,
        f"Trial ending soon for {tenant.name}",
        f"<p>Your trial ends in {days_left} day(s).</p>"
        f"<p><a href='{url_for('cms.settings', category='plan', _external=True)}'>Add payment method</a></p>",
        f"Your trial ends in {days_left} day(s).\n"
        f"Add payment method: {url_for('cms.settings', category='plan', _external=True)}",
    )

    logger.info("Trial ending soon for tenant %s (%d days left)", tenant.id, days_left)


# =============================================================================
# Super-admin: prorated tier change
# =============================================================================

from ..tier_limits import TIERS, TIER_DISPLAY


@cms_bp.route("/admin/tenant/<tenant_id>/change-tier", methods=["POST"])
@login_required
def admin_change_tier(tenant_id: str):
    """Super-admin: change a tenant's tier with Stripe proration.

    For tenants with an active Stripe subscription, this updates the
    subscription item and immediately invoices any prorated amount.
    For free/inactive tenants, it just sets the tier directly.
    """
    if not current_user.is_super_admin:
        abort(403)

    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        abort(404)

    new_tier = (request.form.get("tier") or "").strip().lower()
    if not new_tier or new_tier not in TIERS:
        flash(f"Unknown tier: {new_tier}", "danger")
        return redirect(url_for("cms.tenant_detail", tenant_id=tenant_id))

    old_tier = tenant.tier
    if old_tier == new_tier:
        flash(
            "Tier is already set to " + TIER_DISPLAY.get(new_tier, new_tier.title()),
            "info",
        )
        return redirect(url_for("cms.tenant_detail", tenant_id=tenant_id))

    stripe_invoice_id = None
    amount_cents = 0
    desc = ""

    stripe_conn = _get_stripe()
    if (
        stripe_conn
        and tenant.stripe_subscription_id
        and tenant.subscription_status in ("active", "past_due", "trialing")
    ):
        new_price_id = _tier_to_price(new_tier)
        if new_price_id:
            try:
                sub = stripe.Subscription.retrieve(tenant.stripe_subscription_id)
                items = sub.get("items", {}).get("data", [])
                if items:
                    sub_item_id = items[0].id
                    stripe.Subscription.modify(
                        tenant.stripe_subscription_id,
                        items=[{"id": sub_item_id, "price": new_price_id}],
                        proration_behavior="create_prorations",
                    )

                    # Collect any prorated amount by creating an invoice
                    upcoming = stripe.Invoice.upcoming(
                        customer=tenant.stripe_customer_id,
                        subscription=tenant.stripe_subscription_id,
                    )
                    proration_amount = sum(
                        li.get("amount", 0)
                        for li in upcoming.get("lines", {}).get("data", [])
                        if li.get("proration", False)
                    )

                    if proration_amount > 0:
                        invoice = stripe.Invoice.create(
                            customer=tenant.stripe_customer_id,
                            subscription=tenant.stripe_subscription_id,
                            auto_advance=True,
                        )
                        stripe_invoice_id = invoice.id
                        amount_cents = proration_amount
                        desc = f"Prorated change: {TIER_DISPLAY.get(old_tier, old_tier.title())} → {TIER_DISPLAY.get(new_tier, new_tier.title())}"

            except stripe.error.StripeError as e:
                logger.error("Stripe proration failed for tenant %s: %s", tenant.id, e)
                flash(f"Stripe error: {e.user_message or e}", "danger")
                return redirect(url_for("cms.tenant_detail", tenant_id=tenant_id))

    tenant.tier = new_tier
    db.session.commit()

    log = ProrationLog(
        tenant_id=tenant.id,
        from_tier=old_tier,
        to_tier=new_tier,
        stripe_invoice_id=stripe_invoice_id,
        amount_cents=amount_cents,
        description=desc
        or f"Tier changed: {TIER_DISPLAY.get(old_tier, old_tier.title())} → {TIER_DISPLAY.get(new_tier, new_tier.title())}",
    )
    db.session.add(log)
    db.session.commit()

    flash(
        f"Tier changed from {TIER_DISPLAY.get(old_tier, old_tier.title())} to "
        f"{TIER_DISPLAY.get(new_tier, new_tier.title())}.",
        "success",
    )
    logger.info(
        "Super-admin %s changed tier for tenant %s: %s -> %s (proration=%d cents)",
        current_user.id,
        tenant.id,
        old_tier,
        new_tier,
        amount_cents,
    )
    return redirect(url_for("cms.tenant_detail", tenant_id=tenant_id))
