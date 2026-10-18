import re
import secrets
import unicodedata
from datetime import datetime, timezone
from markupsafe import escape

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import cms_bp
from ..auth import roles_required
from ..models import (
    AuditLog,
    Client,
    ClientPortalAccess,
    CustomerRegistrationRequest,
    Invitation,
    PortalRequest,
    Tenant,
    db,
)
from ..encryption_utils import encryptor
from ..email_utils import is_smtp_configured, send_email


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-") or "customer"
    base = slug[:80]
    candidate = base
    while Tenant.query.filter_by(slug=candidate).first():
        candidate = f"{base[:70]}-{secrets.token_hex(3)}"
    return candidate


def _join_code() -> str:
    while True:
        code = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12].upper()
        if not Tenant.query.filter_by(join_code=code).first():
            return code


def _send_customer_invitation(invitation: Invitation) -> bool:
    """Send a customer invitation through the configured local/production SMTP."""
    if not is_smtp_configured():
        return False
    accept_url = url_for("auth.accept_invite", token=invitation.token, _external=True)
    subject = f"Uitnodiging voor het Iveras klantenportal van {invitation.tenant.name}"
    body_html = f"""
    <html><body style="font-family:sans-serif;padding:2rem;">
      <h2>Toegang tot uw Iveras klantenportal</h2>
      <p>U bent uitgenodigd voor het klantenportal van <strong>{invitation.tenant.name}</strong>.</p>
      <p>Via de onderstaande eenmalige link kiest u uw wachtwoord. Daarna stelt u 2FA in.</p>
      <p><a href="{accept_url}">Uitnodiging accepteren</a></p>
      <p>Deze link is 48 uur geldig.</p>
    </body></html>
    """
    body_text = (
        f"Toegang tot uw Iveras klantenportal\n\n"
        f"Accepteer uw uitnodiging via: {accept_url}\n\n"
        "Deze link is 48 uur geldig."
    )
    return send_email(invitation.email, subject, body_html, body_text)


def _send_customer_response(registration: CustomerRegistrationRequest) -> bool:
    """Send a human response to a public contact question."""
    if not registration.staff_response or not is_smtp_configured():
        return False
    subject = "Reactie op uw vraag aan Iveras"
    response_html = escape(registration.staff_response).replace("\n", "<br>")
    body_html = f"<html><body style=\"font-family:sans-serif;padding:2rem;\"><h2>Reactie van Iveras</h2><p>{response_html}</p><p>Heeft u daarna een onderzoek of klantomgeving nodig? Dien dan een nieuwe aanvraag in via de website.</p></body></html>"
    body_text = f"Reactie van Iveras\n\n{registration.staff_response}\n\nHeeft u daarna een onderzoek of klantomgeving nodig? Dien dan een nieuwe aanvraag in via de website."
    return send_email(registration.email, subject, body_html, body_text)


@cms_bp.route("/portal/registrations", methods=["GET", "POST"])
@login_required
@roles_required("admin", "owner")
def customer_registrations() -> str:
    """Review unassigned registrations and tenant-assigned one-off requests."""
    is_platform_admin = bool(current_user.is_super_admin)
    if request.method == "POST":
        registration = db.session.get(
            CustomerRegistrationRequest, request.form.get("registration_id")
        )
        if not registration:
            flash("Registratieaanvraag niet gevonden.", "error")
            return redirect(url_for("cms.customer_registrations"))
        action = request.form.get("action")
        now = datetime.now(timezone.utc)
        if not is_platform_admin and registration.tenant_id != current_user.tenant_id:
            abort(403)
        if action == "spam":
            if registration.archived_at is not None or registration.status == "spam":
                flash("Deze registratie is al als spam gearchiveerd.", "info")
            else:
                previous_status = registration.status
                registration.status = "spam"
                registration.archived_at = now
                registration.reviewed_by_id = current_user.id
                registration.reviewed_at = now
                AuditLog.log(
                    tenant_id=registration.tenant_id or current_user.tenant_id,
                    user_id=current_user.id,
                    action="customer_registration.spam",
                    entity_type="customer_registration",
                    entity_id=registration.id,
                    description="Customer registration marked as spam and archived; no customer notification sent",
                    old_values={
                        "status": previous_status,
                        "archived_at": None,
                    },
                    new_values={
                        "status": "spam",
                        "archived_at": now.isoformat(),
                    },
                )
                db.session.commit()
                flash("Registratie als spam gemarkeerd en gearchiveerd. Er is geen bericht verzonden.", "success")
            return redirect(url_for("cms.customer_registrations"))
        if registration.archived_at is not None or registration.status == "spam":
            flash("Deze registratie is gearchiveerd en kan niet verder worden verwerkt.", "warning")
            return redirect(url_for("cms.customer_registrations"))
        if action == "respond":
            response_text = (request.form.get("staff_response") or "").strip()
            if registration.registration_type != "other":
                flash("Een antwoord vanuit deze flow is alleen bedoeld voor een andere vraag.", "warning")
            elif not response_text:
                flash("Vul eerst een antwoord in.", "error")
            else:
                registration.staff_response = response_text
                registration.status = "contacted"
                registration.reviewed_by_id = current_user.id
                registration.reviewed_at = now
                db.session.commit()
                email_sent = _send_customer_response(registration)
                AuditLog.log(
                    tenant_id=registration.tenant_id or current_user.tenant_id,
                    user_id=current_user.id,
                    action="customer_registration.responded",
                    entity_type="customer_registration",
                    entity_id=registration.id,
                    description="Staff response saved for customer registration",
                    new_values={"status": "contacted", "email_sent": email_sent},
                )
                db.session.commit()
                if email_sent:
                    flash(f"Antwoord verzonden naar {registration.email}.", "success")
                else:
                    flash("Antwoord opgeslagen. SMTP is niet beschikbaar; verstuur het antwoord handmatig.", "info")
            return redirect(url_for("cms.customer_registrations"))
        if action == "follow_up":
            if registration.registration_type != "other":
                flash("Opvolging is alleen bedoeld voor een andere vraag.", "warning")
            else:
                registration.internal_note = (request.form.get("internal_note") or "").strip() or None
                follow_up = (request.form.get("follow_up_at") or "").strip()
                try:
                    registration.follow_up_at = datetime.strptime(follow_up, "%Y-%m-%d").replace(tzinfo=timezone.utc) if follow_up else None
                except ValueError:
                    flash("Gebruik een geldige opvolgdatum.", "error")
                    return redirect(url_for("cms.customer_registrations"))
                registration.status = "contact_pending"
                AuditLog.log(
                    tenant_id=registration.tenant_id or current_user.tenant_id,
                    user_id=current_user.id,
                    action="customer_registration.follow_up",
                    entity_type="customer_registration",
                    entity_id=registration.id,
                    description="Customer registration follow-up scheduled",
                    new_values={
                        "status": "contact_pending",
                        "follow_up_at": registration.follow_up_at.isoformat() if registration.follow_up_at else None,
                    },
                )
                db.session.commit()
                flash("Opvolging opgeslagen.", "success")
            return redirect(url_for("cms.customer_registrations"))
        if action == "set_type":
            new_type = (request.form.get("new_registration_type") or "").strip()
            if registration.registration_type != "other" or new_type not in {"one_off", "organization"}:
                flash("Kies een geldige vervolgstap.", "error")
            else:
                previous_type = registration.registration_type
                registration.registration_type = new_type
                registration.status = "in_review"
                AuditLog.log(
                    tenant_id=registration.tenant_id or current_user.tenant_id,
                    user_id=current_user.id,
                    action="customer_registration.type_changed",
                    entity_type="customer_registration",
                    entity_id=registration.id,
                    description="Customer registration follow-up type changed",
                    old_values={"registration_type": previous_type},
                    new_values={"registration_type": new_type, "status": "in_review"},
                )
                db.session.commit()
                flash("Vervolgvorm opgeslagen. Je kunt de aanvraag nu normaal goedkeuren.", "success")
            return redirect(url_for("cms.customer_registrations"))
        if action == "assign":
            if not is_platform_admin:
                abort(403)
            tenant = Tenant.query.filter_by(
                id=(request.form.get("tenant_id") or "").strip(), is_active=True
            ).first()
            if not tenant:
                flash("Kies een geldige actieve tenant.", "error")
            elif registration.status in ("approved", "rejected"):
                flash("Deze aanvraag kan niet meer worden toegewezen.", "warning")
            elif registration.registration_type == "organization":
                flash("Een organisatie-aanvraag krijgt bij goedkeuring een nieuwe tenant en kan niet vooraf worden toegewezen.", "warning")
            else:
                registration.tenant_id = tenant.id
                registration.status = "submitted"
                AuditLog.log(
                    tenant_id=tenant.id,
                    user_id=current_user.id,
                    action="customer_registration.assigned",
                    entity_type="customer_registration",
                    entity_id=registration.id,
                    description="Customer registration assigned to investigation tenant",
                    new_values={"tenant_id": tenant.id},
                )
                db.session.commit()
                flash(f"Aanvraag toegewezen aan {tenant.name}.", "success")
        elif not is_platform_admin and registration.tenant_id != current_user.tenant_id:
            abort(403)
        elif not is_platform_admin and registration.registration_type == "organization":
            abort(403)
        elif action == "review" and registration.status == "submitted":
            previous_status = registration.status
            registration.status = "in_review"
            registration.reviewed_by_id = current_user.id
            registration.reviewed_at = now
            AuditLog.log(
                tenant_id=registration.tenant_id or current_user.tenant_id,
                user_id=current_user.id,
                action="customer_registration.review_started",
                entity_type="customer_registration",
                entity_id=registration.id,
                description="Customer registration moved into review",
                old_values={"status": previous_status},
                new_values={"status": "in_review"},
            )
            db.session.commit()
            flash("Registratieaanvraag staat in beoordeling.", "success")
        elif action == "reject" and registration.status not in ("approved", "rejected"):
            previous_status = registration.status
            registration.status = "rejected"
            registration.rejection_reason = (request.form.get("reason") or "").strip() or None
            registration.reviewed_by_id = current_user.id
            registration.reviewed_at = now
            AuditLog.log(
                tenant_id=registration.tenant_id or current_user.tenant_id,
                user_id=current_user.id,
                action="customer_registration.rejected",
                entity_type="customer_registration",
                entity_id=registration.id,
                description="Customer registration rejected",
                old_values={"status": previous_status},
                new_values={"status": "rejected", "reason": registration.rejection_reason},
            )
            db.session.commit()
            flash("Registratieaanvraag afgewezen.", "info")
        elif action == "resend" and registration.status == "approved":
            invitation = (
                Invitation.query.filter(
                    db.func.lower(Invitation.email) == registration.email.lower(),
                    Invitation.accepted_at.is_(None),
                    Invitation.is_portal_invitation.is_(True),
                )
                .order_by(Invitation.created_at.desc())
                .first()
            )
            if not invitation:
                flash("Er staat geen openstaande uitnodiging meer voor deze aanvraag.", "warning")
            elif _send_customer_invitation(invitation):
                AuditLog.log(
                    tenant_id=registration.tenant_id or current_user.tenant_id,
                    user_id=current_user.id,
                    action="customer_registration.invitation_resent",
                    entity_type="customer_registration",
                    entity_id=registration.id,
                    description="Customer portal invitation resent",
                    new_values={"invitation_id": invitation.id, "email_sent": True},
                )
                db.session.commit()
                flash(f"Nieuwe uitnodiging verzonden naar {invitation.email}.", "success")
            else:
                accept_url = url_for("auth.accept_invite", token=invitation.token, _external=True)
                flash(f"SMTP is niet beschikbaar. Uitnodigingslink: {accept_url}", "info")
        elif action == "approve" and registration.status not in ("approved", "rejected"):
            if registration.registration_type == "other":
                registration.status = "handled"
                registration.handled_at = now
                registration.reviewed_by_id = current_user.id
                registration.reviewed_at = now
                AuditLog.log(
                    tenant_id=current_user.tenant_id,
                    user_id=current_user.id,
                    action="customer_registration.question_handled",
                    entity_type="customer_registration",
                    entity_id=registration.id,
                    description="Other customer question marked as handled",
                )
                db.session.commit()
                flash("De andere vraag is als afgehandeld gemarkeerd.", "success")
                return redirect(url_for("cms.customer_registrations"))

            is_organization = registration.registration_type == "organization"
            workspace_type = "full" if is_organization else "portal_only"

            if is_organization:
                tenant = Tenant(
                    name=registration.organization_name,
                    slug=_slugify(registration.organization_name),
                    tier="free",
                    workspace_type=workspace_type,
                    is_active=True,
                    join_code=_join_code(),
                    subscription_status="trialing",
                )
                db.session.add(tenant)
                db.session.flush()
                # The registering organization is a portal customer first.  It
                # receives a client record inside its own tenant so the portal
                # account is scoped to that customer instead of becoming an
                # internal Joost owner with unrestricted tenant visibility.
                client = Client(
                    tenant_id=tenant.id,
                    name=registration.organization_name,
                    is_company=True,
                    contact_person=encryptor.encrypt(registration.contact_name),
                    contact_email=encryptor.encrypt(registration.email),
                    contact_phone=encryptor.encrypt(registration.phone)
                    if registration.phone
                    else None,
                    portal_access_enabled=True,
                    created_by=current_user.id,
                )
                db.session.add(client)
                db.session.flush()
                db.session.add(
                    ClientPortalAccess(
                        tenant_id=tenant.id,
                        client_id=client.id,
                        email=registration.email.strip().lower(),
                        role="portal_admin",
                        created_by_id=current_user.id,
                    )
                )
                invitation = Invitation.create_invitation(
                    tenant_id=tenant.id,
                    email=registration.email,
                    client_id=client.id,
                    role="portal_admin",
                    invited_by_id=current_user.id,
                    is_portal_invitation=True,
                )
            else:
                # One-off customers belong to an existing investigation tenant;
                # they receive client-scoped portal access, not a new tenant.
                tenant = None
                if registration.tenant_id:
                    tenant = Tenant.query.filter_by(
                        id=registration.tenant_id, is_active=True
                    ).first()
                if not tenant:
                    tenant = Tenant.query.filter_by(slug="default", is_active=True).first()
                if not tenant:
                    tenant = db.session.get(Tenant, current_user.tenant_id)
                if not tenant or not tenant.is_active:
                    flash("Geen actieve standaardtenant beschikbaar voor deze klant.", "danger")
                    return redirect(url_for("cms.customer_registrations"))

                client = Client(
                    tenant_id=tenant.id,
                    name=registration.organization_name,
                    is_company=True,
                    contact_person=encryptor.encrypt(registration.contact_name),
                    contact_email=encryptor.encrypt(registration.email),
                    contact_phone=encryptor.encrypt(registration.phone) if registration.phone else None,
                    portal_access_enabled=True,
                    created_by=current_user.id,
                )
                db.session.add(client)
                db.session.flush()
                db.session.add(
                    ClientPortalAccess(
                        tenant_id=tenant.id,
                        client_id=client.id,
                        email=registration.email.strip().lower(),
                        role="portal_viewer",
                        created_by_id=current_user.id,
                    )
                )
                invitation = Invitation.create_invitation(
                    tenant_id=tenant.id,
                    client_id=client.id,
                    email=registration.email,
                    role="portal_viewer",
                    invited_by_id=current_user.id,
                    is_portal_invitation=True,
                )
                # Preserve the original one-off research request in the
                # customer's portal.  The registration creates access before
                # the customer accepts the invitation, so the reviewing user
                # is the initial submitter; the request remains client-scoped
                # and is visible to the invited portal user after login.
                db.session.add(
                    PortalRequest(
                        tenant_id=tenant.id,
                        submitted_by_id=current_user.id,
                        client_id=client.id,
                        request_type="other",
                        question=(
                            registration.message.strip()
                            if registration.message and registration.message.strip()
                            else "Eenmalig OSINT-onderzoek aangevraagd."
                        ),
                        context=(
                            f"Aanvraag via de publieke website door {registration.contact_name}."
                        ),
                        status="submitted",
                        created_at=registration.created_at or now,
                        updated_at=now,
                    )
                )
            registration.status = "approved"
            registration.reviewed_by_id = current_user.id
            registration.reviewed_at = now
            AuditLog.log(
                tenant_id=tenant.id,
                user_id=current_user.id,
                action="customer_registration.approved",
                entity_type="customer_registration",
                entity_id=registration.id,
                description=(
                    "Customer registration approved; tenant, client and portal invitation created"
                    if is_organization
                    else "Customer registration approved; client-scoped portal invitation created"
                ),
                new_values={"tenant_id": tenant.id, "invitation_id": invitation.id},
            )
            db.session.commit()
            if _send_customer_invitation(invitation):
                mail_status = f"Uitnodiging verzonden naar {invitation.email}."
            else:
                accept_url = url_for("auth.accept_invite", token=invitation.token, _external=True)
                mail_status = f"SMTP is niet beschikbaar. Uitnodigingslink: {accept_url}"
            if not is_organization:
                flash(f"Klantrecord en portaltoegang aangemaakt binnen {tenant.name}. {mail_status}", "success")
            else:
                flash(f"Klantomgeving en onderzoekstenant aangemaakt. {mail_status}", "success")
        return redirect(url_for("cms.customer_registrations"))

    show_archived = request.args.get("show_archived") == "1"
    registration_query = CustomerRegistrationRequest.query
    if not is_platform_admin:
        registration_query = registration_query.filter(
            CustomerRegistrationRequest.tenant_id == current_user.tenant_id
        )
    if not show_archived:
        registration_query = registration_query.filter(
            CustomerRegistrationRequest.archived_at.is_(None)
        )
    registrations = registration_query.order_by(
        CustomerRegistrationRequest.created_at.desc()
    ).limit(100).all()
    tenants = []
    if is_platform_admin:
        tenants = Tenant.query.filter_by(is_active=True).order_by(Tenant.name.asc()).all()
    return render_template(
        "cms/customer_registrations.html",
        registrations=registrations,
        tenants=tenants,
        is_platform_admin=is_platform_admin,
        show_archived=show_archived,
    )
