import logging
import threading
import uuid
from datetime import datetime

from cms.models import db, SocialAccount
from cms.workflow.models import (
    WorkflowCase,
    WorkflowResearchAction,
    WorkflowFinding,
    WorkflowScreenshot,
    WorkflowActionFinding,
)

logger = logging.getLogger(__name__)

ACTION_REGISTRY = {}
_running_threads = {}
_threads_lock = threading.Lock()
_credit_lock = threading.Lock()

CREDIT_LIMITS = {
    "tiktok": 50,
    "instagram": 10,
    "linkedin": 50,
    "twitter": 1000,
    "facebook": 30,
}


def _load_credits():
    import json
    from cms.models import Setting

    raw = Setting.get("rapidapi_credits_usage", "{}")
    if isinstance(raw, str):
        return json.loads(raw) if raw.strip() else {}
    return raw if isinstance(raw, dict) else {}


def _save_credits(usage):
    import json
    from cms.models import Setting

    Setting.set("rapidapi_credits_usage", json.dumps(usage))


def get_remaining_credits(action_type):
    with _credit_lock:
        month = datetime.now().strftime("%Y-%m")
        try:
            usage = _load_credits()
            used = usage.get(action_type, {}).get(month, 0)
            limit = CREDIT_LIMITS.get(action_type, 0)
            return max(0, limit - used)
        except Exception:
            logger.warning(
                "Failed to read credit usage for %s", action_type, exc_info=True
            )
            return CREDIT_LIMITS.get(action_type, 0)


def _use_credit(action_type):
    with _credit_lock:
        month = datetime.now().strftime("%Y-%m")
        try:
            usage = _load_credits()
            usage.setdefault(action_type, {})
            usage[action_type][month] = usage[action_type].get(month, 0) + 1
            _save_credits(usage)
        except Exception:
            logger.warning(
                "Failed to record credit usage for %s", action_type, exc_info=True
            )


def _has_credits(action_type):
    return get_remaining_credits(action_type) > 0


def register_action(
    action_type, label, icon, handler, description="", category="open", cost_label=""
):
    ACTION_REGISTRY[action_type] = {
        "label": label,
        "icon": icon,
        "handler": handler,
        "description": description,
        "category": category,
        "cost_label": cost_label,
    }


def action_category(action_type):
    """Return the action's channel category: 'paid', 'open' or 'local'."""
    return ACTION_REGISTRY.get(action_type, {}).get("category", "open")


def is_paid_action(action_type):
    return action_category(action_type) == "paid"


def paid_channels_enabled(tenant_id=None):
    """Paid research channels are OFF by default (ADR-0001 D1.6).

    They are only enabled for a tenant via an explicit ``FeatureFlag``
    override named ``paid_channels`` (super-admin config). Without an
    override the channels stay disabled regardless of tier.
    """
    from cms.models import FeatureFlag

    tid = tenant_id
    if not tid:
        from flask import g, has_app_context

        if has_app_context():
            tid = getattr(g, "tenant_id", None)
        if not tid:
            try:
                from flask_login import current_user

                if (
                    current_user.is_authenticated
                    and getattr(current_user, "tenant", None) is not None
                ):
                    tid = current_user.tenant.id
            except Exception:
                pass
    if not tid:
        return False
    try:
        override = FeatureFlag.query.filter_by(
            tenant_id=tid, flag_name="paid_channels"
        ).first()
    except Exception:
        logger.debug("FeatureFlag lookup failed", exc_info=True)
        return False
    return bool(override.enabled) if override is not None else False


def cancel_action(action_id):
    with _threads_lock:
        if action_id in _running_threads:
            _running_threads[action_id]["cancel"] = True
    action = db.session.get(WorkflowResearchAction, action_id)
    if action and action.status == "running":
        action.status = "cancelled"
        action.completed_at = datetime.now()
        action.result_summary = "Cancelled"
        # Soft-delete the produced findings instead of hard-deleting them: the
        # evidence (screenshots, links, integrity hashes) must survive for
        # audit/integrity purposes. Soft-deleted findings are filtered out of
        # every list view.
        links = WorkflowActionFinding.query.filter_by(action_id=action_id).all()
        if links:
            finding_ids = [link.finding_id for link in links]
            findings = WorkflowFinding.query.filter(
                WorkflowFinding.id.in_(finding_ids),
            ).all()
            for finding in findings:
                finding.soft_delete()
        db.session.commit()


def is_action_cancelled(action_id):
    with _threads_lock:
        return action_id in _running_threads and _running_threads[action_id].get(
            "cancel"
        )


def run_action(action_id):
    from cms.tenant_context import set_tenant_context

    try:
        # Cold worker: no request context, so there is no flask.g tenant for
        # RLS to scope the first read. Look the action up under a temporary
        # bypass and read only its tenant_id, then drop the bypass.
        set_tenant_context(db, None, bypass_rls=True)
        try:
            action = db.session.get(WorkflowResearchAction, action_id)
        finally:
            set_tenant_context(db, None)
        if not action:
            return

        tenant_id = action.tenant_id
        if not tenant_id:
            # Fail closed: an action without a tenant must never run or create
            # unattributed rows.
            action.status = "error"
            action.error = "Action has no tenant_id"
            db.session.commit()
            return

        # Scope the whole run to the action's tenant and reload the row under
        # that context (the bypass lookup above loaded it un-scoped).
        set_tenant_context(db, tenant_id)
        db.session.expire(action)
        action = db.session.get(WorkflowResearchAction, action_id)
        if not action:
            return

        # ADR-0001 D1.6: paid channels are off by default behind explicit tenant
        # config. Re-checked at execution time so a flag that was switched off
        # after proposal creation still blocks the run.
        if is_paid_action(action.action_type) and not paid_channels_enabled(
            action.tenant_id
        ):
            action.status = "error"
            action.error = "Paid channels are disabled for this tenant"
            db.session.commit()
            return

        action.status = "running"
        action.started_at = datetime.now()
        db.session.commit()

        entry = ACTION_REGISTRY.get(action.action_type)
        if not entry:
            action.status = "error"
            action.error = f"Unknown action type: {action.action_type}"
            db.session.commit()
            return

        # Resolve who created this action for finding.created_by
        action_creator_id = getattr(action, "created_by", None)
        if not action_creator_id:
            case = db.session.get(WorkflowCase, action.case_id)
            action_creator_id = case.created_by if case else None

        # Decrypt subject identifiers so handlers can read plaintext. Keep
        # autoflush off across the handler: a handler query (e.g. resolving the
        # target subject or recording an OSINT call) would otherwise autoflush
        # and the before_flush guard would re-encrypt the decrypted identifiers
        # mid-handler.
        with db.session.no_autoflush:
            _case = db.session.get(WorkflowCase, action.case_id)
            if _case:
                for _s in _case.subjects:
                    try:
                        _s.decrypt_identifiers()
                    except Exception:
                        logger.debug(
                            "Failed to decrypt identifiers for subject %s",
                            _s.id,
                            exc_info=True,
                        )

            findings_data = entry["handler"](action)

        # Re-encrypt identifiers the handlers may have decrypted in-place before
        # any commit. encrypt_identifiers() is idempotent, so untouched fields
        # stay as-is. Without this the same ORM subjects would be persisted
        # as plaintext on the commits below.
        if _case:
            for _s in _case.subjects:
                try:
                    _s.encrypt_identifiers()
                except Exception:
                    logger.debug(
                        "Failed to re-encrypt identifiers for subject %s",
                        _s.id,
                        exc_info=True,
                    )

        if is_action_cancelled(action_id):
            action.status = "cancelled"
            action.completed_at = datetime.now()
            action.result_summary = "Cancelled"
            db.session.commit()
            return

        created = []
        for fd in findings_data:
            detail_text = fd.get("detail", "")
            subject_id = fd.get("subject_id")
            finding = WorkflowFinding(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                case_id=action.case_id,
                subject_id=subject_id,
                title=fd["title"],
                content=detail_text or fd["title"],
                detail=detail_text,
                source_url=fd.get("source_url"),
                source_type=fd.get("source_type", action.action_type),
                icon=fd.get("icon", entry["icon"]),
                verified=fd.get("verified", False),
                raw_data=fd.get("raw_data"),
                created_by=action_creator_id,
                created_at=datetime.now(),
            )
            db.session.add(finding)
            db.session.flush()

            link = WorkflowActionFinding(action_id=action.id, finding_id=finding.id)
            db.session.add(link)

            for ss in fd.get("screenshots", []):
                screenshot = WorkflowScreenshot(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    finding_id=finding.id,
                    url=ss.get("url"),
                    source_url=ss.get("source_url"),
                    file_path=ss.get("file_path"),
                    captured_at=datetime.now(),
                )
                db.session.add(screenshot)

            sa_data = fd.get("social_account")
            if sa_data and subject_id:
                dedup = SocialAccount.query.filter(
                    SocialAccount.subject_id == subject_id,
                    SocialAccount.platform == sa_data["platform"],
                    SocialAccount.username == sa_data["username"],
                ).first()
                if not dedup:
                    db.session.add(
                        SocialAccount(
                            subject_id=subject_id,
                            tenant_id=tenant_id,
                            platform=sa_data["platform"],
                            username=sa_data["username"],
                            url=sa_data.get("url"),
                            account_id=sa_data.get("account_id"),
                            finding_id=finding.id,
                        )
                    )

            created.append(finding)

        action.status = "completed"
        action.completed_at = datetime.now()
        action.result_summary = f"{len(created)} findings"
        db.session.commit()

        from cms.services.invoice_service import auto_invoice_action_completed

        auto_invoice_action_completed(action)

    except Exception as e:
        logger.exception("Research action failed: %s", action_id)
        db.session.rollback()
        action = db.session.get(WorkflowResearchAction, action_id)
        if action:
            action.status = "error"
            action.error = str(e)
            db.session.commit()
    finally:
        # A pooled worker connection must not carry a tenant context (or a
        # bypass flag) into the next task; always reset it. On PostgreSQL this
        # also guarantees app.tenant_id exists for any following RLS query.
        set_tenant_context(db, None)


def start_action_async(action_id):
    from flask import current_app

    try:
        _app = current_app._get_current_object()
    except RuntimeError:
        _app = None

    def _run():
        try:
            if _app:
                with _app.app_context():
                    run_action(action_id)
            else:
                run_action(action_id)
        finally:
            with _threads_lock:
                _running_threads.pop(action_id, None)

    t = threading.Thread(target=_run, args=(), daemon=True)
    t.start()
    with _threads_lock:
        _running_threads[action_id] = {"thread": t, "cancel": False}
