from .response import api_error

"""
App-level system routes: health, version, config, docs, error handlers.
Registered directly on the Flask app (not the CMS blueprint).
"""

import json
import logging
import os
import re
import signal
import uuid
from datetime import datetime, timezone

import flask
from flask import (
    Flask,
    Response,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import safe_join, secure_filename
from cms.rate_limiting import rate_limit

logger = logging.getLogger(__name__)


def _portal_profile_complete(client) -> bool:
    """Return whether the customer card has the minimum portal details."""
    required_fields = (
        "contact_person",
        "contact_email",
        "contact_phone",
        "address_street",
        "address_number",
        "address_postal",
        "address_city",
        "address_country",
    )
    return all(bool(getattr(client, field, None)) for field in required_fields)


def register_system_routes(app: Flask) -> None:
    """Register system-level routes on the Flask app."""

    def _public_site_hosts() -> set[str]:
        configured = os.environ.get("PUBLIC_SITE_HOSTS", "iveras.com,www.iveras.com")
        return {host.strip().lower() for host in configured.split(",") if host.strip()}

    @app.route("/")
    def index() -> flask.Response:
        host = (request.host or "").split(":", 1)[0].lower()
        if host in _public_site_hosts():
            return public_homepage()
        return redirect(url_for("cms.dashboard"))

    @app.route("/public")
    def public_homepage() -> str:
        """Render the public website prototype without changing the CMS root."""
        return render_template(
            "public/homepage.html",
            public_site_url=os.environ.get("PUBLIC_SITE_URL", "https://iveras.com"),
        )

    @app.route("/contact")
    def public_contact() -> str:
        """Explain the available customer routes before intake."""
        return render_template("public/contact.html")

    @app.route("/privacy")
    def public_privacy() -> str:
        """Public privacy notice for the marketing site and portal intake."""
        return render_template(
            "public/legal.html",
            page="privacy",
            public_site_url=os.environ.get("PUBLIC_SITE_URL", "https://iveras.com"),
        )

    @app.route("/voorwaarden")
    def public_terms() -> str:
        """Public general terms page; final legal review remains required."""
        return render_template(
            "public/legal.html",
            page="terms",
            public_site_url=os.environ.get("PUBLIC_SITE_URL", "https://iveras.com"),
        )

    @app.route("/cookies")
    def public_cookies() -> str:
        """Explain the limited cookie use on the public site and portal."""
        return render_template(
            "public/legal.html",
            page="cookies",
            public_site_url=os.environ.get("PUBLIC_SITE_URL", "https://iveras.com"),
        )

    @app.route("/robots.txt")
    def public_robots() -> Response:
        """Keep private application and portal routes out of search engines."""
        site_url = os.environ.get("PUBLIC_SITE_URL", "https://iveras.com").rstrip("/")
        body = (
            "User-agent: *\n"
            "Allow: /\n"
            "Allow: /privacy\n"
            "Allow: /voorwaarden\n"
            "Allow: /cookies\n"
            "Allow: /contact\n"
            "Disallow: /auth/\n"
            "Disallow: /cms/\n"
            "Disallow: /portal/\n"
            f"Sitemap: {site_url}/sitemap.xml\n"
        )
        return Response(body, mimetype="text/plain")

    @app.route("/sitemap.xml")
    def public_sitemap() -> Response:
        """Expose only public, indexable pages in the XML sitemap."""
        site_url = os.environ.get("PUBLIC_SITE_URL", "https://iveras.com").rstrip("/")
        urls = ("/", "/privacy", "/voorwaarden", "/cookies", "/contact")
        body = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        body += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        for path in urls:
            body += f"  <url><loc>{site_url}{path}</loc></url>\n"
        body += "</urlset>\n"
        return Response(body, mimetype="application/xml")

    @app.route("/portal")
    @login_required
    def portal_entry() -> flask.Response:
        """Open the authenticated, tenant-scoped customer portal.

        The old public prototype used synthetic records and made approved
        customer requests invisible.  Keep the short, memorable URL as the
        portal entrypoint, but always send authenticated users to the real
        tenant-scoped dashboard.
        """
        return redirect(url_for("portal_dashboard"))

    @app.route("/portal/register", methods=["GET", "POST"])
    @rate_limit(limit=(5, 300), key_prefix="customer_registration")
    def portal_customer_registration() -> str:
        """Public registration request; it never creates access directly."""
        from cms.models import CustomerRegistrationRequest, db
        from cms.services.turnstile import is_enabled, verify

        if request.method == "POST":
            if (request.form.get("website") or "").strip():
                return render_template(
                    "public/portal_register.html",
                    error="De aanvraag kon niet worden verwerkt.",
                    form=request.form,
                    turnstile_site_key=current_app.config.get("TURNSTILE_SITE_KEY", ""),
                    turnstile_enabled=is_enabled(),
                ), 400
            if is_enabled():
                verified, reason = verify(
                    request.form.get("cf-turnstile-response")
                    or request.form.get("cf-turnstile"),
                    request.remote_addr,
                )
                if not verified:
                    message = (
                        "Bevestig eerst dat u geen geautomatiseerd formulier gebruikt."
                        if reason in {"missing_token", "rejected"}
                        else "De beveiligingscontrole kon niet worden afgerond. Probeer het opnieuw."
                    )
                    return render_template(
                        "public/portal_register.html",
                        error=message,
                        form=request.form,
                        turnstile_site_key=current_app.config.get("TURNSTILE_SITE_KEY", ""),
                        turnstile_enabled=is_enabled(),
                    ), 400
            organization_name = (request.form.get("organization_name") or "").strip()
            contact_name = (request.form.get("contact_name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            registration_type = (request.form.get("registration_type") or "one_off").strip()
            phone = (request.form.get("phone") or "").strip() or None
            message = (request.form.get("message") or "").strip() or None
            if registration_type not in {"one_off", "organization", "other"}:
                registration_type = "one_off"
            organization_required = registration_type == "organization"
            if (organization_required and not organization_name) or not contact_name or "@" not in email:
                return render_template(
                    "public/portal_register.html",
                    error="Vul de organisatienaam, contactpersoon en een geldig e-mailadres in." if organization_required else "Vul contactpersoon en een geldig e-mailadres in.",
                    form=request.form,
                    turnstile_site_key=current_app.config.get("TURNSTILE_SITE_KEY", ""),
                    turnstile_enabled=is_enabled(),
                ), 400
            if registration_type in {"one_off", "other"} and not message:
                return render_template(
                    "public/portal_register.html",
                    error="Beschrijf kort uw onderzoeksvraag of andere vraag.",
                    form=request.form,
                    turnstile_site_key=current_app.config.get("TURNSTILE_SITE_KEY", ""),
                    turnstile_enabled=is_enabled(),
                ), 400
            existing = CustomerRegistrationRequest.query.filter(
                CustomerRegistrationRequest.email == email,
                CustomerRegistrationRequest.status.in_(["submitted", "in_review", "approved"]),
            ).first()
            if existing:
                return render_template(
                    "public/portal_register.html",
                    error="Voor dit e-mailadres staat al een aanvraag open.",
                    form=request.form,
                    turnstile_site_key=current_app.config.get("TURNSTILE_SITE_KEY", ""),
                    turnstile_enabled=is_enabled(),
                ), 409
            db.session.add(
                CustomerRegistrationRequest(
                    organization_name=organization_name or contact_name,
                    registration_type=registration_type,
                    contact_name=contact_name,
                    email=email,
                    phone=phone,
                    message=message,
                    status="submitted",
                )
            )
            db.session.commit()
            from cms.services.portal_notifications import notify_registration_received
            registration = CustomerRegistrationRequest.query.filter_by(
                email=email, status="submitted"
            ).order_by(CustomerRegistrationRequest.created_at.desc()).first()
            if registration:
                notify_registration_received(registration)
            return render_template("public/portal_register.html", submitted=True)
        from cms.services.turnstile import is_enabled

        return render_template(
            "public/portal_register.html",
            form={},
            turnstile_site_key=current_app.config.get("TURNSTILE_SITE_KEY", ""),
            turnstile_enabled=is_enabled(),
        )

    @app.route("/portal/dashboard")
    @login_required
    def portal_dashboard() -> str:
        """Render the authenticated, tenant-scoped portal dashboard MVP."""
        from cms.models import Case, Client, Document, Invoice, Notification, PortalRequest
        from cms import db
        from cms.services.client_portal import portal_client_ids
        from cms.services.usage_metering import record_usage_event

        allowed_client_ids = portal_client_ids(current_user)
        portal_clients = []
        if allowed_client_ids is not None:
            portal_clients = (
                Client.query.filter(
                    Client.id.in_(allowed_client_ids),
                    Client.is_deleted.is_(False),
                )
                .order_by(Client.name)
                .all()
            )
        if allowed_client_ids is not None:
            profile_client = (
                Client.query.filter(
                    Client.id.in_(allowed_client_ids),
                    Client.is_deleted.is_(False),
                )
                .order_by(Client.name)
                .all()
            )
            profile_client = next(
                (
                    client
                    for client in profile_client
                    if not _portal_profile_complete(client)
                ),
                None,
            )
            if profile_client and request.args.get("profile_saved") != "1":
                return redirect(
                    url_for("portal_client_profile", client_id=profile_client.id)
                )
        cases_query = Case.query.filter_by(tenant_id=current_user.tenant_id, is_deleted=False).filter(Case.archived_at.is_(None))
        requests_query = PortalRequest.query.filter_by(tenant_id=current_user.tenant_id)
        invoices_query = Invoice.query.filter_by(tenant_id=current_user.tenant_id, is_deleted=False)
        if allowed_client_ids is not None:
            cases_query = cases_query.filter(Case.client_id.in_(allowed_client_ids))
            requests_query = requests_query.filter(PortalRequest.client_id.in_(allowed_client_ids))
            invoices_query = invoices_query.filter(Invoice.client_id.in_(allowed_client_ids))
        cases = cases_query.order_by(Case.updated_at.desc(), Case.created_at.desc()).limit(50).all()
        public_document_counts = {
            case.id: Document.query.filter_by(
                case_id=case.id,
                tenant_id=current_user.tenant_id,
                is_deleted=False,
                classification="public",
            ).count()
            for case in cases
        }
        requests = requests_query.order_by(PortalRequest.created_at.desc()).limit(50).all()
        invoices = invoices_query.filter(Invoice.status.notin_(["draft", "cancelled"])).order_by(Invoice.due_date.desc(), Invoice.created_at.desc()).limit(50).all()
        notifications = (
            Notification.query.filter_by(
                tenant_id=current_user.tenant_id, user_id=current_user.id
            )
            .order_by(Notification.created_at.desc())
            .limit(20)
            .all()
        )
        record_usage_event(
            tenant_id=current_user.tenant_id,
            metric_name="portal_dashboard_views",
            audit_action="portal.dashboard.viewed",
            entity_type="portal_dashboard",
            user_id=current_user.id,
            description="Customer portal dashboard viewed",
        )
        db.session.commit()
        return render_template(
            "public/portal_dashboard.html",
            cases=cases,
            portal_requests=requests,
            invoices=invoices,
            notifications=notifications,
            portal_clients=portal_clients,
            tenant_name=current_user.tenant.name if current_user.tenant else "",
            submitted=request.args.get("submitted"),
            request_message=request.args.get("request_message") == "1",
            public_document_counts=public_document_counts,
        )

    @app.route("/portal/notifications/<notification_id>/read", methods=["POST"])
    @login_required
    @rate_limit(limit=(30, 60), key_prefix="portal_notification_read")
    def portal_notification_read(notification_id: str):
        """Mark one own portal notification as read."""
        from cms import db
        from cms.models import Notification

        notification = Notification.query.filter_by(
            id=notification_id,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
        ).first_or_404()
        notification.is_read = True
        db.session.commit()
        return redirect(url_for("portal_dashboard"))

    @app.route("/portal/profile/<client_id>", methods=["GET", "POST"])
    @login_required
    @rate_limit(limit=(20, 300), key_prefix="portal_profile")
    def portal_client_profile(client_id: str) -> str:
        """Let a portal customer complete only their own customer card."""
        from cms import db
        from cms.encryption_utils import encryptor
        from cms.models import AuditLog, Client
        from cms.services.client_portal import portal_can_access_client

        client = Client.query.filter_by(
            id=client_id,
            tenant_id=current_user.tenant_id,
            is_deleted=False,
        ).first_or_404()
        if not portal_can_access_client(current_user, client.id):
            abort(404)

        encrypted_fields = {
            "contact_person",
            "contact_email",
            "contact_phone",
            "address_street",
            "address_number",
            "address_postal",
            "address_city",
            "address_country",
            "vat_number",
        }

        def display_value(field: str) -> str:
            value = getattr(client, field) or ""
            if field in encrypted_fields and value:
                try:
                    return encryptor.decrypt(value)
                except Exception:
                    return ""
            return value

        if request.method == "POST":
            fields = {
                "contact_person": (request.form.get("contact_person") or "").strip(),
                "contact_email": (request.form.get("contact_email") or "").strip().lower(),
                "contact_phone": (request.form.get("contact_phone") or "").strip(),
                "address_street": (request.form.get("address_street") or "").strip(),
                "address_number": (request.form.get("address_number") or "").strip(),
                "address_postal": (request.form.get("address_postal") or "").strip(),
                "address_city": (request.form.get("address_city") or "").strip(),
                "address_country": (request.form.get("address_country") or "").strip(),
                "vat_number": (request.form.get("vat_number") or "").strip(),
            }
            required = (
                "contact_person",
                "contact_email",
                "contact_phone",
                "address_street",
                "address_number",
                "address_postal",
                "address_city",
                "address_country",
            )
            if any(not fields[field] for field in required) or "@" not in fields["contact_email"]:
                return render_template(
                    "public/portal_profile.html",
                    client=client,
                    form=fields,
                    error="Vul alle gemarkeerde contact- en adresgegevens in.",
                ), 400

            for field, value in fields.items():
                setattr(client, field, encryptor.encrypt(value) if value else None)
            AuditLog.log(
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                action="portal.client_profile_updated",
                entity_type="client",
                entity_id=client.id,
                description="Customer completed their portal profile",
            )
            db.session.commit()
            return redirect(url_for("portal_dashboard", profile_saved="1"))

        return render_template(
            "public/portal_profile.html",
            client=client,
            form={field: display_value(field) for field in encrypted_fields},
        )

    @app.route("/portal/cases/<case_id>/report")
    @login_required
    def portal_case_report(case_id: str) -> str:
        """Show only the released report for a case in the customer portal."""
        import bleach
        import markdown as md_lib

        from cms.models import Case, db
        from cms.services.client_portal import portal_can_access_client
        from cms.services.report_findings import normalize_report_markdown
        from cms.services.usage_metering import record_usage_event

        case = Case.query.filter_by(
            id=case_id,
            tenant_id=current_user.tenant_id,
            is_deleted=False,
        ).first_or_404()
        if not portal_can_access_client(current_user, case.client_id):
            return render_template("public/portal_unavailable.html", title="Geen toegang", message="Dit dossier is niet aan uw klantprofiel gekoppeld."), 404
        if not case.pv_body or case.report_status != "released":
            return render_template(
                "public/portal_unavailable.html",
                title="Rapport nog niet beschikbaar",
                message="De onderzoeker heeft het rapport nog niet vrijgegeven.",
            ), 404

        raw_html = md_lib.markdown(
            normalize_report_markdown(case.pv_body), extensions=["tables"]
        )
        report_html = bleach.clean(
            raw_html,
            tags=[
                "p", "br", "strong", "em", "a", "ul", "ol", "li", "h1",
                "h2", "h3", "h4", "h5", "h6", "code", "pre", "blockquote",
                "hr", "table", "thead", "tbody", "tr", "th", "td",
            ],
            attributes={"a": ["href", "title"], "th": ["align"], "td": ["align"]},
        )
        record_usage_event(
            tenant_id=current_user.tenant_id,
            metric_name="portal_reports_viewed",
            audit_action="portal.report.viewed",
            entity_type="case_report",
            entity_id=case.id,
            case_id=case.id,
            user_id=current_user.id,
            description="Customer viewed a released case report",
        )
        db.session.commit()
        return render_template(
            "public/portal_report.html", case=case, report_html=report_html
        )

    @app.route("/portal/cases/<case_id>/report/download")
    @login_required
    @rate_limit(limit=(20, 300), key_prefix="portal_report_download")
    def portal_case_report_download(case_id: str):
        """Download a released report without exposing internal case data."""
        import html
        import io
        import bleach
        import markdown as md_lib
        from flask import send_file

        from cms.models import Case, db
        from cms.services.client_portal import portal_can_access_client
        from cms.services.report_findings import normalize_report_markdown
        from cms.services.usage_metering import record_usage_event

        case = Case.query.filter_by(
            id=case_id,
            tenant_id=current_user.tenant_id,
            is_deleted=False,
        ).first_or_404()
        if not portal_can_access_client(current_user, case.client_id):
            abort(404)
        if not case.pv_body or case.report_status != "released":
            return render_template(
                "public/portal_unavailable.html",
                title="Rapport nog niet beschikbaar",
                message="De onderzoeker heeft het rapport nog niet vrijgegeven.",
            ), 404

        raw_html = md_lib.markdown(
            normalize_report_markdown(case.pv_body), extensions=["tables"]
        )
        report_html = bleach.clean(
            raw_html,
            tags=[
                "p", "br", "strong", "em", "a", "ul", "ol", "li", "h1",
                "h2", "h3", "h4", "h5", "h6", "code", "pre", "blockquote",
                "hr", "table", "thead", "tbody", "tr", "th", "td",
            ],
            attributes={"a": ["href", "title"], "th": ["align"], "td": ["align"]},
        )
        document = f"""<!doctype html>
<html lang=\"nl\"><head><meta charset=\"utf-8\"><title>Rapport {html.escape(case.case_number)}</title>
<style>body{{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;line-height:1.5;color:#17213f}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #dfe5f1;padding:8px;text-align:left}}a{{color:#2856c7}}</style></head>
<body><h1>Rapport {html.escape(case.case_number)}</h1><p>{html.escape(case.title)} · vrijgegeven rapport</p>{report_html}</body></html>"""
        record_usage_event(
            tenant_id=current_user.tenant_id,
            metric_name="portal_reports_downloaded",
            audit_action="portal.report.downloaded",
            entity_type="case_report",
            entity_id=case.id,
            case_id=case.id,
            user_id=current_user.id,
            description="Customer downloaded a released case report",
        )
        db.session.commit()
        return send_file(
            io.BytesIO(document.encode("utf-8")),
            mimetype="text/html",
            as_attachment=True,
            download_name=f"rapport-{case.case_number}.html",
        )

    @app.route("/portal/cases/<case_id>/documents")
    @login_required
    def portal_case_documents(case_id: str) -> str:
        """List only documents explicitly published to the customer portal."""
        from cms.models import Case, Document
        from cms.services.client_portal import portal_can_access_client

        case = Case.query.filter_by(
            id=case_id, tenant_id=current_user.tenant_id, is_deleted=False
        ).first_or_404()
        if not portal_can_access_client(current_user, case.client_id):
            abort(404)
        documents = (
            Document.query.filter_by(
                case_id=case_id,
                tenant_id=current_user.tenant_id,
                is_deleted=False,
                classification="public",
            )
            .order_by(Document.created_at.desc())
            .all()
        )
        return render_template(
            "public/portal_documents.html", case_id=case_id, documents=documents
        )

    @app.route("/portal/cases/<case_id>/messages", methods=["GET", "POST"])
    @login_required
    def portal_case_messages(case_id: str) -> str:
        """Secure two-way chat between a portal customer and the case handler."""
        from cms.models import AuditLog, Case, CaseMessage, Notification, db
        from cms.services.client_portal import portal_can_access_client
        from cms.routes.case_messages import _notify_staff

        case = Case.query.filter_by(
            id=case_id, tenant_id=current_user.tenant_id, is_deleted=False
        ).first_or_404()
        if not portal_can_access_client(current_user, case.client_id):
            return render_template(
                "public/portal_unavailable.html",
                title="Geen toegang",
                message="Dit dossier is niet aan uw klantprofiel gekoppeld.",
            ), 404

        if request.method == "POST":
            text = (request.form.get("message") or "").strip()
            if not text:
                flask.flash("Schrijf eerst een bericht.", "error")
            else:
                message = CaseMessage(
                    tenant_id=case.tenant_id,
                    case_id=case.id,
                    sender_user_id=current_user.id,
                    sender_role="customer",
                    message=text,
                )
                db.session.add(message)
                db.session.flush()
                _notify_staff(case, text)
                AuditLog.log(
                    user_id=current_user.id,
                    tenant_id=case.tenant_id,
                    action="case.message.sent",
                    entity_type="case_message",
                    entity_id=message.id,
                    case_id=case.id,
                    description="Portal customer sent a message to the case handler",
                )
                db.session.commit()
                flask.flash("Bericht naar de behandelaar verzonden.", "success")
            return redirect(url_for("portal_case_messages", case_id=case.id))

        messages = (
            CaseMessage.query.filter_by(tenant_id=case.tenant_id, case_id=case.id)
            .order_by(CaseMessage.created_at.asc())
            .all()
        )
        CaseMessage.query.filter_by(
            tenant_id=case.tenant_id,
            case_id=case.id,
            sender_role="staff",
            is_read=False,
        ).update({"is_read": True}, synchronize_session=False)
        Notification.query.filter(
            Notification.tenant_id == case.tenant_id,
            Notification.user_id == current_user.id,
            Notification.category == "case_updates",
            Notification.is_read.is_(False),
            Notification.link.like(f"%/portal/cases/{case.id}/messages"),
        ).update({"is_read": True}, synchronize_session=False)
        db.session.commit()
        return render_template(
            "public/portal_case_messages.html", case=case, messages=messages
        )

    @app.route("/portal/documents/<document_id>/download")
    @login_required
    def portal_document_download(document_id: str):
        """Download a document only when it is explicitly portal-visible."""
        import os
        from flask import abort, current_app, send_file

        from cms.models import Document
        from cms.models import Case
        from cms.services.client_portal import portal_can_access_client
        from cms.services.usage_metering import record_usage_event
        from cms import db

        document = Document.query.filter_by(
            id=document_id,
            tenant_id=current_user.tenant_id,
            is_deleted=False,
            classification="public",
        ).first_or_404()
        case = Case.query.filter_by(id=document.case_id, tenant_id=current_user.tenant_id, is_deleted=False).first_or_404()
        if not portal_can_access_client(current_user, case.client_id):
            abort(404)
        if not document.storage_path:
            abort(404)
        storage_path = (document.storage_path or "").replace("\\", "/")
        if not (
            storage_path.startswith("uploads/cases/")
            or storage_path.startswith("uploads/portal-requests/")
        ):
            abort(404)
        static_root = os.path.join(current_app.root_path, "static")
        file_path = safe_join(static_root, storage_path)
        if not file_path or not os.path.isfile(file_path):
            abort(404)
        record_usage_event(
            tenant_id=current_user.tenant_id,
            metric_name="portal_documents_downloaded",
            audit_action="portal.document.downloaded",
            entity_type="document",
            entity_id=document.id,
            user_id=current_user.id,
            description="Customer downloaded a published document",
        )
        db.session.commit()
        return send_file(
            file_path,
            as_attachment=True,
            download_name=document.original_filename,
        )

    @app.route("/portal/invoices/<invoice_id>")
    @login_required
    def portal_invoice(invoice_id: str) -> str:
        """Show a tenant-scoped, customer-visible invoice."""
        from cms.models import Invoice, db
        from cms.services.client_portal import portal_client_ids
        from cms.services.usage_metering import record_usage_event

        invoice = Invoice.query.filter_by(
            id=invoice_id,
            tenant_id=current_user.tenant_id,
            is_deleted=False,
        ).filter(Invoice.status.notin_(["draft", "cancelled"])).first_or_404()
        allowed_client_ids = portal_client_ids(current_user)
        if allowed_client_ids is not None and invoice.client_id not in allowed_client_ids:
            from flask import abort
            abort(404)
        record_usage_event(
            tenant_id=current_user.tenant_id,
            metric_name="portal_invoices_viewed",
            audit_action="portal.invoice.viewed",
            entity_type="invoice",
            entity_id=invoice.id,
            user_id=current_user.id,
            description="Customer viewed an invoice",
        )
        db.session.commit()
        return render_template("public/portal_invoice.html", invoice=invoice)

    @app.route("/portal/new-request", methods=["GET", "POST"])
    @login_required
    @rate_limit(limit=(10, 300), key_prefix="portal_new_request")
    def portal_request_prototype() -> str:
        """Submit a tenant-scoped customer intake request."""
        from cms.models import PortalRequest, db
        from cms.models import Client
        from cms.services.client_portal import portal_client_ids
        from cms.services.usage_metering import record_usage_event

        if request.method == "POST":
            request_type = (request.form.get("request_type") or "").strip()
            question = (request.form.get("question") or "").strip()
            context = (request.form.get("context") or "").strip() or None
            deadline = (request.form.get("deadline") or "").strip() or None
            allowed_types = {"person", "organization", "digital_trace", "other"}
            allowed_client_ids = portal_client_ids(current_user)
            requested_client_id = (request.form.get("client_id") or "").strip() or None
            if allowed_client_ids is not None and not requested_client_id and len(allowed_client_ids) == 1:
                requested_client_id = allowed_client_ids[0]
            if allowed_client_ids is not None and requested_client_id not in allowed_client_ids:
                return render_template("public/portal_request.html", error="Kies een geldige klantrelatie.", form=request.form, portal_clients=Client.query.filter(Client.id.in_(allowed_client_ids)).order_by(Client.name).all()), 400
            if request_type not in allowed_types or not question:
                return render_template(
                    "public/portal_request.html",
                    error="Kies een geldig type en vul de onderzoeksvraag in.",
                    form=request.form,
                    portal_clients=Client.query.filter(Client.id.in_(allowed_client_ids)).order_by(Client.name).all() if allowed_client_ids is not None else [],
                ), 400

            portal_request = PortalRequest(
                tenant_id=current_user.tenant_id,
                submitted_by_id=current_user.id,
                client_id=requested_client_id,
                request_type=request_type,
                question=question,
                context=context,
                deadline=deadline,
                status="submitted",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            normalized_question = re.sub(r"\s+", " ", question.casefold()).strip()
            recent_requests = (
                PortalRequest.query.filter_by(
                    tenant_id=current_user.tenant_id,
                    submitted_by_id=current_user.id,
                )
                .filter(PortalRequest.status != "declined")
                .order_by(PortalRequest.created_at.desc())
                .limit(50)
                .all()
            )
            for previous in recent_requests:
                previous_question = re.sub(
                    r"\s+", " ", (previous.question or "").casefold()
                ).strip()
                if previous_question and previous_question == normalized_question:
                    portal_request.duplicate_of_id = previous.id
                    break
            db.session.add(portal_request)
            db.session.flush()
            record_usage_event(
                tenant_id=current_user.tenant_id,
                metric_name="portal_requests_submitted",
                audit_action="portal.request.submitted",
                entity_type="portal_request",
                entity_id=portal_request.id,
                user_id=current_user.id,
                description="Customer portal request submitted",
            )
            db.session.commit()
            from cms.services.portal_notifications import notify_tenant_admins
            notify_tenant_admins(
                portal_request,
                "Nieuwe klantaanvraag in Iveras",
                "Nieuwe portal-aanvraag",
                f"Er is een nieuwe aanvraag ingediend door {current_user.email}.",
                url_for("cms.portal_requests", _external=True),
            )
            return redirect(url_for("portal_dashboard", submitted=portal_request.id))

        allowed_client_ids = portal_client_ids(current_user)
        portal_clients = Client.query.filter(Client.id.in_(allowed_client_ids)).order_by(Client.name).all() if allowed_client_ids is not None else []
        return render_template("public/portal_request.html", form={}, portal_clients=portal_clients)

    @app.route("/portal/requests/<request_id>/message", methods=["GET", "POST"])
    @login_required
    @rate_limit(limit=(20, 300), key_prefix="portal_request_message")
    def portal_request_message(request_id: str) -> str:
        """Allow a portal customer to add information to their own request."""
        from cms import db
        from cms.models import Document, PortalRequest, PortalRequestMessage
        from cms.image_validation import validate_upload
        from cms.services.client_portal import portal_can_access_client
        from cms.services.portal_notifications import notify_tenant_admins
        from cms.services.usage_metering import record_usage_event

        intake = PortalRequest.query.filter_by(
            id=request_id, tenant_id=current_user.tenant_id
        ).first_or_404()
        if not portal_can_access_client(current_user, intake.client_id):
            abort(404)
        if intake.status in ("converted", "declined"):
            return render_template(
                "public/portal_request_message.html",
                intake=intake,
                attachments=Document.query.filter_by(
                    portal_request_id=intake.id, tenant_id=current_user.tenant_id, is_deleted=False
                ).order_by(Document.created_at.desc()).all(),
                error="Deze aanvraag kan geen aanvullende informatie meer ontvangen.",
            ), 400
        if request.method == "POST":
            message = (request.form.get("message") or "").strip()
            upload = request.files.get("file")
            filename = (upload.filename or "").strip() if upload else ""
            if not message and not filename:
                return render_template(
                    "public/portal_request_message.html",
                    intake=intake,
                    attachments=Document.query.filter_by(
                        portal_request_id=intake.id, tenant_id=current_user.tenant_id, is_deleted=False
                    ).order_by(Document.created_at.desc()).all(),
                    error="Vul aanvullende informatie in of voeg een bestand toe.",
                ), 400
            if len(message) > 10000:
                return render_template(
                        "public/portal_request_message.html",
                        intake=intake,
                        attachments=[],
                        error="Het bericht mag maximaal 10.000 tekens bevatten.",
                ), 400
            if filename:
                allowed_extensions = {"pdf", "png", "jpg", "jpeg", "doc", "docx", "xls", "xlsx", "txt", "csv"}
                if "." not in filename or filename.rsplit(".", 1)[1].lower() not in allowed_extensions:
                    return render_template(
                        "public/portal_request_message.html",
                        intake=intake,
                        attachments=[],
                        error="Dit bestandstype is niet toegestaan. Gebruik pdf, document-, spreadsheet-, tekst- of afbeeldingsbestanden.",
                    ), 400
                file_ext = filename.rsplit(".", 1)[1].lower()
                valid, detected = validate_upload(upload, file_ext)
                if not valid:
                    return render_template(
                        "public/portal_request_message.html",
                        intake=intake,
                        attachments=[],
                        error=f"Het bestand lijkt niet overeen te komen met de extensie ({detected or 'onbekend formaat'}).",
                    ), 400
                upload.seek(0, 2)
                file_size = upload.tell()
                upload.seek(0)
                if file_size > 16 * 1024 * 1024:
                    return render_template(
                        "public/portal_request_message.html",
                        intake=intake,
                        attachments=[],
                        error="Een bestand mag maximaal 16 MB groot zijn.",
                    ), 400
                safe_original = secure_filename(filename) or f"bijlage.{file_ext}"
                unique_filename = f"{uuid.uuid4().hex}.{file_ext}"
                relative_dir = f"uploads/portal-requests/{intake.id}"
                upload_dir = safe_join(current_app.root_path, "static", relative_dir)
                if not upload_dir:
                    abort(400)
                os.makedirs(upload_dir, exist_ok=True)
                upload_path = safe_join(upload_dir, unique_filename)
                if not upload_path:
                    abort(400)
                upload.save(upload_path)
                db.session.add(
                    Document(
                        tenant_id=current_user.tenant_id,
                        portal_request_id=intake.id,
                        filename=unique_filename,
                        original_filename=safe_original,
                        mime_type=upload.content_type,
                        file_size=os.path.getsize(upload_path),
                        storage_path=f"{relative_dir}/{unique_filename}",
                        storage_type="local",
                        document_type="portal_request_attachment",
                        classification="confidential",
                        uploaded_by=current_user.id,
                    )
                )
                record_usage_event(
                    tenant_id=current_user.tenant_id,
                    metric_name="portal_request_attachments_uploaded",
                    audit_action="portal.request.attachment_uploaded",
                    entity_type="portal_request",
                    entity_id=intake.id,
                    user_id=current_user.id,
                    description="Customer uploaded a portal request attachment",
                )
            if message:
                db.session.add(
                    PortalRequestMessage(
                        tenant_id=current_user.tenant_id,
                        request_id=intake.id,
                        author_user_id=current_user.id,
                        message=message,
                    )
                )
                record_usage_event(
                    tenant_id=current_user.tenant_id,
                    metric_name="portal_request_messages_added",
                    audit_action="portal.request.message_added",
                    entity_type="portal_request",
                    entity_id=intake.id,
                    user_id=current_user.id,
                    description="Customer added information to a portal request",
                )
            # Make the workflow state visible to the requester: new customer
            # information is now waiting for internal review.
            intake.status = "additional_info"
            db.session.commit()
            notify_tenant_admins(
                intake,
                "Aanvullende informatie bij portal-aanvraag",
                "Nieuwe informatie bij een klantaanvraag",
                f"De klant heeft aanvullende informatie toegevoegd aan aanvraag {intake.id}.",
                url_for("cms.portal_requests", _external=True),
            )
            return redirect(url_for("portal_dashboard", request_message="1"))
        return render_template(
            "public/portal_request_message.html",
            intake=intake,
            attachments=Document.query.filter_by(
                portal_request_id=intake.id, tenant_id=current_user.tenant_id, is_deleted=False
            ).order_by(Document.created_at.desc()).all(),
        )

    @app.route("/portal/requests/<request_id>/documents/<document_id>/download")
    @login_required
    @rate_limit(limit=(60, 60), key_prefix="portal_request_document_download")
    def portal_request_document_download(request_id: str, document_id: str):
        """Download an attachment belonging to the authenticated customer's request."""
        from flask import send_file

        from cms import db
        from cms.models import Document, PortalRequest
        from cms.services.client_portal import portal_can_access_client
        from cms.services.usage_metering import record_usage_event

        intake = PortalRequest.query.filter_by(
            id=request_id, tenant_id=current_user.tenant_id
        ).first_or_404()
        if not portal_can_access_client(current_user, intake.client_id):
            abort(404)
        document = Document.query.filter_by(
            id=document_id,
            portal_request_id=intake.id,
            tenant_id=current_user.tenant_id,
            is_deleted=False,
        ).first_or_404()
        if not document.storage_path:
            abort(404)
        storage_path = (document.storage_path or "").replace("\\", "/")
        if not storage_path.startswith("uploads/portal-requests/"):
            abort(404)
        static_root = os.path.join(current_app.root_path, "static")
        file_path = safe_join(static_root, storage_path)
        if not file_path or not os.path.isfile(file_path):
            abort(404)
        record_usage_event(
            tenant_id=current_user.tenant_id,
            metric_name="portal_request_attachments_downloaded",
            audit_action="portal.request.attachment_downloaded",
            entity_type="document",
            entity_id=document.id,
            user_id=current_user.id,
            description="Customer downloaded a portal request attachment",
        )
        db.session.commit()
        return send_file(
            file_path,
            as_attachment=True,
            download_name=document.original_filename,
        )

    @app.route("/api/version", methods=["GET"])
    @login_required
    def get_version() -> flask.Response:
        from version import get_version_info

        return jsonify(get_version_info())

    @app.route("/api/changelog", methods=["GET"])
    @login_required
    def get_changelog() -> flask.Response:
        import os

        _root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        cl_path = os.path.join(_root, "CHANGELOG.md")
        logger.info("CHANGELOG path: %s exists: %s", cl_path, os.path.exists(cl_path))
        if not os.path.exists(cl_path):
            return jsonify({"html": "<p>No changelog available.</p>"})
        with open(cl_path) as f:
            raw = f.read()
        logger.info("CHANGELOG read %d bytes", len(raw))
        lines = raw.split("\n")
        html_parts = []
        for line in lines:
            if line.startswith("### "):
                html_parts.append(f"<p><strong>{line[4:]}</strong></p>")
            elif line.startswith("## "):
                html_parts.append(f"<h3>{line[3:]}</h3>")
            elif line.startswith("- "):
                html_parts.append(f"<li>{line[2:]}</li>")
            elif line.strip() == "":
                html_parts.append("<br>")
            else:
                html_parts.append(f"<p>{line}</p>")
        result = "".join(html_parts)
        logger.info("CHANGELOG html length: %d", len(result))
        return jsonify({"html": result})

    @app.route("/api/config", methods=["GET"])
    @login_required
    def get_config_api() -> flask.Response:
        from cms.models import Setting

        return jsonify(
            {
                "overheid_key_configured": bool(Setting.get("overheid_api_key")),
                "twochat_configured": bool(
                    Setting.get("twochat_api_key")
                    and Setting.get("twochat_whatsapp_number")
                ),
                "hibp_key_configured": bool(Setting.get("hibp_api_key")),
                "ai_available": _check_ollama_available(),
            }
        )

    @app.route("/api/docs")
    @login_required
    def api_docs() -> str:
        return render_template("cms/api_docs.html")

    @app.route("/api/openapi.json")
    @login_required
    def openapi_spec() -> flask.Response:
        spec = _build_openapi_spec()
        return jsonify(spec)

    @app.route("/api/rate-limit-status", methods=["GET"])
    @login_required
    def rate_limit_status() -> flask.Response:
        from cms.rate_limiting import get_api_rate_limit_status, get_rate_limit_status

        return jsonify(
            {
                "api_rate_limits": get_api_rate_limit_status(),
                "platform_rate_limits": get_rate_limit_status(),
            }
        )

    @app.route("/health")
    def health_check() -> flask.Response:
        from cms.models import Setting, db
        from cms.health_utils import check_external_services

        status = {"status": "ok"}
        http_status = 200

        quick = request.args.get("quick") == "1"
        if quick:
            # The dashboard polls this endpoint frequently; never run network
            # checks or write settings from the request worker.
            try:
                snapshot = json.loads(Setting.get("health_snapshot", ""))
                svc = snapshot.get("services", {})
                if not isinstance(svc, dict):
                    svc = {}
            except (TypeError, ValueError):
                svc = {}
            try:
                db.session.execute(db.text("SELECT 1"))
                svc["database"] = "ok"
            except Exception as exc:
                svc["database"] = f"error: {exc}"
        else:
            svc = check_external_services()
        relabel = {"ok": "connected"}
        database_label = relabel.get(
            svc.get("database", ""), svc.get("database", "unknown")
        )
        status["database"] = database_label
        status["spiderfoot"] = svc.get("spiderfoot", "unknown")
        status["spiderfoot_cached_ok"] = Setting.get("spiderfoot_last_ok", "never")
        for key in ("rdw", "kadaster", "hibp", "overheid", "brave", "tor"):
            if key in svc:
                status[key] = svc[key]

        from cms.cache import get_status as cache_status

        status["cache"] = cache_status()

        # Disk and memory checks (psutil optional — x86_64 binary on ARM macOS)
        try:
            import psutil

            disk = psutil.disk_usage("/")
            status["disk"] = {
                "total_gb": round(disk.total / (1024**3), 1),
                "used_gb": round(disk.used / (1024**3), 1),
                "free_gb": round(disk.free / (1024**3), 1),
                "percent_used": disk.percent,
            }
            if disk.percent > 90:
                status["status"] = "degraded"
                http_status = 503

            memory = psutil.virtual_memory()
            status["memory"] = {
                "total_gb": round(memory.total / (1024**3), 1),
                "available_gb": round(memory.available / (1024**3), 1),
                "percent_used": memory.percent,
            }
        except ImportError:
            pass

        # Redis check (when configured)
        redis_url = flask.current_app.config.get("REDIS_URL", "")
        if redis_url:
            try:
                import redis as redis_lib

                r = redis_lib.from_url(redis_url, socket_connect_timeout=3)
                r.ping()
                r.close()
                status["redis"] = "connected"
            except Exception:
                status["redis"] = "disconnected"
                status["status"] = "degraded"
                http_status = 503

        # Database health
        if svc.get("database") != "ok":
            status["status"] = "degraded"
            http_status = 503

        if not quick:
            try:
                from cms.models import db
                from sqlalchemy import text

                db.session.execute(text("SELECT 1"))
                status["db_ping"] = "ok"
            except Exception:
                status["db_ping"] = "error"
                status["status"] = "degraded"
                http_status = 503

            # Alembic migration sync (readiness)
            from cms.health_utils import check_migrations

            migrations = check_migrations()
            status["migrations"] = migrations
            if migrations != "ok":
                status["status"] = "degraded"
                http_status = 503

        return jsonify(status), http_status

    @app.errorhandler(404)
    def not_found_error(e) -> flask.Response:
        if request.path.startswith("/api/"):
            return api_error("Not found", 404)
        return render_template("cms/404.html"), 404

    @app.errorhandler(500)
    def internal_error(e) -> flask.Response:
        logger.exception("Internal server error")
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error"}), 500
        return render_template("cms/500.html"), 500

    @app.errorhandler(413)
    def request_entity_too_large(e) -> flask.Response:
        return jsonify({"error": "Request too large (max 16MB)"}), 413

    @app.route("/api/keep-alive", methods=["POST"])
    def session_keep_alive() -> flask.Response:
        """Extend the current session lifetime.

        Called periodically by the client-side session timeout warning JS.
        Returns the remaining session lifetime in seconds.
        """
        from flask import session

        session.modified = True
        remaining = int(app.permanent_session_lifetime.total_seconds())
        return jsonify({"status": "ok", "remaining": remaining})

    @app.before_request
    def log_request_info() -> None:
        request.request_id = getattr(g, "request_id", "-")
        if request.path.startswith("/api/"):
            logger.info("=> %s %s", request.method, request.path)

    @app.after_request
    def log_response_info(response) -> flask.Response:
        if request.path.startswith("/api/"):
            logger.info(
                "<= %s %s — %s", request.method, request.path, response.status_code
            )
        return response

    # Graceful shutdown
    def _handle_shutdown(signum, frame) -> None:
        logger.info("Received signal %s, shutting down...", signum)
        from cms.rate_limiting import cleanup_rate_limits

        cleanup_rate_limits(0)

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info("System routes registered")


def _check_ollama_available() -> bool:
    """Check if any AI provider (OpenRouter or Ollama) is available."""
    from cms.services.ai_service import check_ai_available

    return check_ai_available()


def _build_openapi_spec() -> dict:
    """Build OpenAPI 3.0.3 specification dynamically from Flask's URL map."""
    from flask import current_app

    paths = {}
    static_rules = {"static", "flask-compress.cache"}

    for rule in sorted(current_app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.rule.startswith("/static") or rule.endpoint in static_rules:
            continue
        endpoint_name = (
            rule.endpoint.split(".")[-1] if "." in rule.endpoint else rule.endpoint
        )
        methods = {
            m for m in rule.methods if m in ("GET", "POST", "PUT", "PATCH", "DELETE")
        }
        if not methods:
            continue
        path_item = paths.setdefault(rule.rule, {})
        for method in sorted(methods):
            method_lower = method.lower()
            path_item[method_lower] = {
                "summary": f"{method} {rule.rule}",
                "operationId": f"{endpoint_name}_{method_lower}",
                "parameters": [
                    {
                        "name": p,
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                    for p in _extract_path_params(rule.rule)
                ],
                "responses": {
                    "200": {"description": "Success"},
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                    "403": {"description": "Forbidden"},
                    "404": {"description": "Not found"},
                    "500": {"description": "Internal server error"},
                },
            }

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "OSINT Dashboard API",
            "version": "1.0.0",
            "description": "REST API for Iveras OSINT Dashboard — all endpoints auto-discovered from Flask routes.",
        },
        "servers": [{"url": "/", "description": "Local server"}],
        "paths": paths,
    }


def _extract_path_params(rule: str) -> list[str]:
    """Extract path parameter names from a Flask rule string."""
    import re

    return re.findall(r"<(?:\w+:)?(\w+)>", rule)
