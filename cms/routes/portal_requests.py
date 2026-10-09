from datetime import date

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import cms_bp
from ..auth import roles_required
from ..models import Case, CaseStatus, Client, Document, PortalRequest, User, db
from ..services.usage_metering import record_usage_event
from ..services.portal_notifications import notify_assigned_reviewer, notify_requester


@cms_bp.route("/portal/requests", methods=["GET", "POST"])
@login_required
@roles_required("admin", "owner", "intake_admin", "senior_investigator", "investigator")
def portal_requests() -> str:
    """Review tenant-scoped customer portal requests."""
    if request.method == "POST":
        request_id = (request.form.get("request_id") or "").strip()
        action = (request.form.get("action") or "").strip()
        intake = PortalRequest.query.filter_by(
            id=request_id, tenant_id=current_user.tenant_id
        ).first_or_404()

        if action == "reject":
            intake.status = "declined"
            record_usage_event(
                tenant_id=current_user.tenant_id,
                metric_name="portal_requests_declined",
                audit_action="portal.request.declined",
                entity_type="portal_request",
                entity_id=intake.id,
                user_id=current_user.id,
                description="Portal request declined",
            )
            db.session.commit()
            notify_requester(
                intake,
                "Update over uw Iveras-aanvraag",
                "Uw aanvraag is afgewezen",
                "Uw aanvraag is door Iveras afgewezen. Neem contact op als u toelichting nodig heeft.",
                url_for("portal_dashboard", _external=True),
            )
            flash("Aanvraag afgewezen.", "info")
        elif action == "assign":
            assigned_to_id = (request.form.get("assigned_to_id") or "").strip()
            reviewer = User.query.filter_by(
                id=assigned_to_id, tenant_id=current_user.tenant_id, is_active=True
            ).first()
            reviewer_roles = {"owner", "admin", "intake_admin", "senior_investigator", "investigator"}
            if not reviewer or reviewer.role not in reviewer_roles:
                flash("Kies een actieve behandelaar uit dezelfde organisatie.", "error")
            else:
                intake.assigned_to_id = reviewer.id
                record_usage_event(
                    tenant_id=current_user.tenant_id,
                    metric_name="portal_requests_assigned",
                    audit_action="portal.request.assigned",
                    entity_type="portal_request",
                    entity_id=intake.id,
                    user_id=current_user.id,
                    description=f"Portal request assigned to {reviewer.full_name or reviewer.email}",
                )
                db.session.commit()
                notify_assigned_reviewer(
                    intake,
                    "Nieuwe portal-aanvraag aan u toegewezen",
                    "Portal-aanvraag aan u toegewezen",
                    f"De aanvraag '{intake.question[:160]}' staat voor u klaar ter beoordeling.",
                    url_for("cms.portal_requests", _external=True),
                )
                flash(f"Aanvraag toegewezen aan {reviewer.full_name or reviewer.email}.", "success")
        elif action == "review":
            intake.status = "in_review"
            record_usage_event(
                tenant_id=current_user.tenant_id,
                metric_name="portal_requests_reviewed",
                audit_action="portal.request.reviewed",
                entity_type="portal_request",
                entity_id=intake.id,
                user_id=current_user.id,
                description="Portal request moved to review",
            )
            db.session.commit()
            notify_requester(
                intake,
                "Update over uw Iveras-aanvraag",
                "Uw aanvraag wordt beoordeeld",
                "Uw aanvraag staat nu in beoordeling bij Iveras.",
                url_for("portal_dashboard", _external=True),
            )
            flash("Aanvraag staat nu in beoordeling.", "success")
        elif action == "convert":
            if intake.status == "converted":
                flash("Deze aanvraag is al omgezet naar een zaak.", "info")
            elif intake.status == "declined":
                flash("Een afgewezen aanvraag kan niet meer worden omgezet.", "warning")
            else:
                client_id = (request.form.get("client_id") or "").strip()
                client = Client.query.filter_by(
                    id=client_id, tenant_id=current_user.tenant_id, is_deleted=False
                ).first()
                if not client:
                    flash("Kies een geldige klant uit dezelfde organisatie.", "error")
                else:
                    assigned_to_id = (request.form.get("assigned_to_id") or intake.assigned_to_id or "").strip()
                    handler = User.query.filter_by(
                        id=assigned_to_id, tenant_id=current_user.tenant_id, is_active=True
                    ).first() if assigned_to_id else None
                    handler_roles = {"owner", "admin", "senior_investigator", "investigator", "junior_investigator"}
                    if current_user.role == "intake_admin" and not handler:
                        flash("Wijs eerst een onderzoeker toe voordat u de aanvraag omzet naar een zaak.", "warning")
                        return redirect(url_for("cms.portal_requests"))
                    if assigned_to_id and (not handler or handler.role not in handler_roles):
                        flash("Kies een actieve behandelaar uit dezelfde organisatie.", "error")
                        return redirect(url_for("cms.portal_requests"))
                    case_description = intake.question.strip()
                    if intake.context:
                        case_description += f"\n\nContext van de klant:\n{intake.context.strip()}"
                    if intake.deadline:
                        case_description += f"\n\nGewenste termijn: {intake.deadline.strip()}"
                    case = Case(
                        case_number=Case.generate_case_number(current_user.tenant_id),
                        client_id=client.id,
                        title=(request.form.get("title") or intake.question[:300]).strip(),
                        description=case_description,
                        status=CaseStatus.OPEN.value,
                        start_date=date.today(),
                        case_type=intake.request_type,
                        created_by=(handler.id if handler else (None if current_user.role == "intake_admin" else current_user.id)),
                        assigned_to=handler.id if handler else None,
                        lead_investigator_id=handler.id if handler else None,
                    )
                    db.session.add(case)
                    intake.status = "converted"
                    intake.client_id = client.id
                    if handler:
                        intake.assigned_to_id = handler.id
                    db.session.flush()
                    intake.case_id = case.id
                    # Keep uploaded intake evidence available in the case while
                    # retaining its original portal-request link for traceability.
                    for document in Document.query.filter_by(
                        portal_request_id=intake.id, is_deleted=False
                    ).all():
                        document.case_id = case.id
                    record_usage_event(
                        tenant_id=current_user.tenant_id,
                        metric_name="portal_requests_converted",
                        audit_action="portal.request.converted",
                        entity_type="portal_request",
                        entity_id=intake.id,
                        user_id=current_user.id,
                        case_id=case.id,
                        description="Portal request converted to case",
                    )
                    db.session.commit()
                    if handler:
                        notify_assigned_reviewer(
                            intake,
                            "Nieuwe zaak aan u toegewezen",
                            "Nieuwe zaak aan u toegewezen",
                            f"De portal-aanvraag is omgezet naar zaak {case.case_number} en aan u toegewezen.",
                            url_for("cms.view_case", case_id=case.id, _external=True),
                        )
                    notify_requester(
                        intake,
                        "Uw Iveras-aanvraag is omgezet",
                        "Uw aanvraag is omgezet naar een zaak",
                        f"Uw aanvraag is omgezet naar zaak {case.case_number}.",
                        url_for("portal_dashboard", _external=True),
                    )
                    flash(f"Aanvraag omgezet naar zaak {case.case_number}.", "success")
        else:
            flash("Onbekende actie.", "error")
        return redirect(url_for("cms.portal_requests"))

    requests = (
        PortalRequest.query.filter_by(tenant_id=current_user.tenant_id)
        .order_by(PortalRequest.created_at.desc())
        .limit(100)
        .all()
    )
    clients = (
        Client.query.filter_by(tenant_id=current_user.tenant_id, is_deleted=False)
        .order_by(Client.name.asc())
        .all()
    )
    reviewer_roles = {"owner", "admin", "intake_admin", "senior_investigator", "investigator", "junior_investigator"}
    reviewers = (
        User.query.filter_by(tenant_id=current_user.tenant_id, is_active=True)
        .filter(User.role.in_(reviewer_roles))
        .order_by(User.full_name.asc(), User.email.asc())
        .all()
    )
    handlers = [reviewer for reviewer in reviewers if reviewer.role != "intake_admin"]
    return render_template(
        "cms/portal_requests.html", portal_requests=requests, clients=clients, reviewers=reviewers, handlers=handlers
    )
