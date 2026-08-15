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


def register_action(action_type, label, icon, handler, description="", category="open"):
    ACTION_REGISTRY[action_type] = {
        "label": label,
        "icon": icon,
        "handler": handler,
        "description": description,
        "category": category,
    }


def action_category(action_type):
    """Return the action's channel category: 'paid', 'open' or 'local'."""
    return ACTION_REGISTRY.get(action_type, {}).get("category", "open")


def is_paid_action(action_type):
    return action_category(action_type) == "paid"


def cancel_action(action_id):
    with _threads_lock:
        if action_id in _running_threads:
            _running_threads[action_id]["cancel"] = True
    action = db.session.get(WorkflowResearchAction, action_id)
    if action and action.status == "running":
        action.status = "cancelled"
        action.completed_at = datetime.now()
        action.result_summary = "Cancelled"
        links = WorkflowActionFinding.query.filter_by(action_id=action_id).all()
        if links:
            finding_ids = [link.finding_id for link in links]
            WorkflowFinding.query.filter(
                WorkflowFinding.id.in_(finding_ids),
            ).delete(synchronize_session=False)
            WorkflowActionFinding.query.filter_by(action_id=action_id).delete()
        db.session.commit()


def is_action_cancelled(action_id):
    with _threads_lock:
        return action_id in _running_threads and _running_threads[action_id].get(
            "cancel"
        )


def run_action(action_id):
    try:
        action = db.session.get(WorkflowResearchAction, action_id)
        if not action:
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

        # Decrypt subject identifiers so handlers can read plaintext
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
