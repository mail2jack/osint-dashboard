import logging
import json
from datetime import datetime, timezone

import stripe
from flask import Blueprint, redirect, request, jsonify, current_app, url_for, flash
from flask_login import login_required, current_user

from ..models import db, Tenant
from ..auth import admin_required
from .. import csrf
from . import cms_bp

logger = logging.getLogger(__name__)

stripe_bp = Blueprint("stripe", __name__, url_prefix="/stripe")


def _price_to_tier() -> dict[str, str]:
    """Return mapping of Stripe Price ID → tier name.

    Override via PlatformSetting key 'stripe_price_mapping' (JSON dict).
    Falls back to empty dict — tier must be set manually or via webhook.
    """
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
        session = stripe_mod.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            client_reference_id=tenant.id,
            customer_email=current_user.email,
            metadata={
                "tenant_id": tenant.id,
                "tier": tier,
            },
            success_url=success_url,
            cancel_url=cancel_url,
        )
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
        else:
            logger.info("Stripe webhook: unhandled event %s", event_type)
    except Exception as e:
        logger.exception("Stripe webhook handler error for %s: %s", event_type, e)
        return jsonify({"error": "Internal error"}), 500

    return jsonify({"status": "ok"}), 200


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
    db.session.commit()
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

    tenant = Tenant.query.filter_by(stripe_subscription_id=sub_id).first()
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

    if status == "active" and items:
        price_id = items[0].get("price", {}).get("id", "")
        tier = _price_to_tier().get(price_id)
        if tier:
            tenant.tier = tier

    tenant.subscription_status = status
    db.session.commit()
    logger.info("Tenant %s subscription %s → %s", tenant.id, sub_id, status)


def _handle_subscription_deleted(subscription: dict):
    sub_id = subscription.get("id")
    tenant = Tenant.query.filter_by(stripe_subscription_id=sub_id).first()
    if not tenant:
        logger.warning("subscription.deleted: no tenant for sub %s", sub_id)
        return

    tenant.subscription_status = "canceled"
    tenant.tier = "free"
    tenant.stripe_subscription_id = None
    tenant.current_period_end = None
    db.session.commit()
    logger.info("Tenant %s subscription %s canceled → free", tenant.id, sub_id)
