"""Secure two-way messaging for customers and internal case handlers."""

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import cms_bp
from ..auth import roles_required
from ..models import AuditLog, Case, CaseMessage, ClientPortalAccess, Notification, db
from ..services.portal_notifications import _send


def _notify_staff(case, message):
    from ..models import User

    if case.lead_investigator_id:
        staff = User.query.filter_by(
            id=case.lead_investigator_id,
            tenant_id=case.tenant_id,
            is_active=True,
        ).all()
    else:
        staff = User.query.filter(
            User.tenant_id == case.tenant_id,
            User.is_active.is_(True),
            User.role.in_(["admin", "owner"]),
        ).all()
    link = url_for("cms.staff_case_messages", case_id=case.id, _external=True)
    for user in staff:
        db.session.add(Notification(
            tenant_id=case.tenant_id, user_id=user.id, category="case_updates",
            title="Nieuw klantbericht", message=f"Nieuw bericht bij zaak {case.case_number}.", link=link,
        ))


def _notify_customer(case, message):
    accesses = ClientPortalAccess.query.filter_by(
        tenant_id=case.tenant_id, client_id=case.client_id, is_active=True
    ).all()
    link = url_for("portal_case_messages", case_id=case.id, _external=True)
    for access in accesses:
        if access.user_id:
            db.session.add(Notification(
                tenant_id=case.tenant_id, user_id=access.user_id, category="case_updates",
                title="Nieuw bericht van Iveras", message=f"Er staat een nieuw bericht klaar bij zaak {case.case_number}.", link=link,
            ))
        _send(access.email, f"Nieuw bericht bij zaak {case.case_number}", "Nieuw bericht van Iveras", f"Er staat een nieuw bericht klaar bij zaak {case.case_number}. Log in op het portal om het bericht te lezen.", link)


@cms_bp.route("/workflow/case/<case_id>/messages", methods=["GET", "POST"])
@login_required
@roles_required("admin", "owner", "senior_investigator", "investigator")
def staff_case_messages(case_id):
    case = Case.query.filter_by(id=case_id, tenant_id=current_user.tenant_id, is_deleted=False).first_or_404()
    if request.method == "POST":
        text = (request.form.get("message") or "").strip()
        if not text:
            flash("Schrijf eerst een bericht.", "error")
        else:
            message = CaseMessage(tenant_id=case.tenant_id, case_id=case.id, sender_user_id=current_user.id, sender_role="staff", message=text)
            db.session.add(message)
            db.session.flush()
            _notify_customer(case, text)
            AuditLog.log(
                user_id=current_user.id,
                tenant_id=case.tenant_id,
                action="case.message.sent",
                entity_type="case_message",
                entity_id=message.id,
                case_id=case.id,
                description="Internal user sent a message to the customer",
            )
            db.session.commit()
            flash("Bericht naar de klant verzonden.", "success")
        return redirect(url_for("cms.staff_case_messages", case_id=case.id))
    messages = CaseMessage.query.filter_by(tenant_id=case.tenant_id, case_id=case.id).order_by(CaseMessage.created_at.asc()).all()
    CaseMessage.query.filter_by(tenant_id=case.tenant_id, case_id=case.id, sender_role="customer", is_read=False).update({"is_read": True}, synchronize_session=False)
    db.session.commit()
    return render_template("cms/workflow/workflow_case_messages.html", case=case, messages=messages)
