"""Synthetic customer registration and portal-only access checks."""

from datetime import date
from io import BytesIO
from pathlib import Path

from cms.models import (
    Case,
    CaseMessage,
    Client,
    CustomerRegistrationRequest,
    ClientPortalAccess,
    Document,
    AuditLog,
    Invitation,
    Invoice,
    InvoiceItem,
    Notification,
    PortalRequest,
    PortalRequestMessage,
    Tenant,
    User,
    db,
)


def _login_as(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_admin_can_mark_registration_as_spam_without_notification_and_audit(
    auth_client, db_session
):
    admin = User.query.filter_by(username="admin").one()
    registration = CustomerRegistrationRequest(
        tenant_id=admin.tenant_id,
        organization_name="Synthetic spam registration",
        contact_name="Synthetic spammer",
        email="synthetic-spam@example.test",
        registration_type="other",
        message="Unwanted synthetic intake.",
    )
    db_session.add(registration)
    db_session.commit()

    response = auth_client.post(
        "/cms/portal/registrations",
        data={"registration_id": registration.id, "action": "spam"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    db_session.refresh(registration)
    assert registration.status == "spam"
    assert registration.archived_at is not None
    assert Invitation.query.filter_by(email=registration.email).count() == 0

    audit = AuditLog.query.filter_by(
        entity_type="customer_registration",
        entity_id=registration.id,
        action="customer_registration.spam",
    ).one()
    assert audit.new_values["status"] == "spam"
    assert audit.new_values["archived_at"]
    assert "no customer notification sent" in audit.description

    active_page = auth_client.get("/cms/portal/registrations")
    assert registration.email.encode() not in active_page.data
    archived_page = auth_client.get("/cms/portal/registrations?show_archived=1")
    assert registration.email.encode() in archived_page.data
    assert b"SPAM" in archived_page.data


def test_one_off_customer_gets_client_portal_access_and_completes_profile(
    auth_client, db_session
):
    response = auth_client.post(
        "/portal/register",
        data={
            "organization_name": "Synthetische Eenmalige Klant",
            "contact_name": "Test Klant",
            "email": "synthetic-customer@example.test",
            "registration_type": "one_off",
            "message": "Testregistratie zonder productiegegevens.",
        },
    )
    assert response.status_code == 200

    registration = CustomerRegistrationRequest.query.filter_by(
        email="synthetic-customer@example.test"
    ).one()
    assert registration.status == "submitted"

    response = auth_client.post(
        "/cms/portal/registrations",
        data={"registration_id": registration.id, "action": "approve"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    invitation = Invitation.query.filter_by(
        email="synthetic-customer@example.test"
    ).one()
    tenant = Tenant.query.get(invitation.tenant_id)
    assert tenant.slug == "default"
    assert tenant.workspace_type == "full"
    assert invitation.role == "portal_viewer"
    assert invitation.is_portal_invitation is True
    assert invitation.client_id is not None
    portal_access = ClientPortalAccess.query.filter_by(
        client_id=invitation.client_id,
        email="synthetic-customer@example.test",
    ).one()
    assert portal_access.user_id is None
    portal_request = PortalRequest.query.filter_by(
        tenant_id=tenant.id,
        client_id=invitation.client_id,
    ).one()
    assert portal_request.status == "submitted"
    assert portal_request.question == "Testregistratie zonder productiegegevens."

    response = auth_client.post(
        f"/auth/accept-invite/{invitation.token}",
        data={
            "full_name": "Test Klant",
            "password": "Synthetic123!",
            "confirm_password": "Synthetic123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    customer = User.query.filter_by(
        email="synthetic-customer@example.test"
    ).one()
    assert customer.role == "portal_viewer"
    assert customer.tenant_id == tenant.id
    assert portal_access.user_id == customer.id

    repeat_invitation = Invitation.create_invitation(
        tenant_id=tenant.id,
        client_id=invitation.client_id,
        email=customer.email,
        role="portal_viewer",
        invited_by_id=User.query.filter_by(username="admin").one().id,
        is_portal_invitation=True,
    )
    db.session.commit()
    repeat_response = auth_client.post(
        f"/auth/accept-invite/{repeat_invitation.token}",
        data={
            "full_name": "Test Klant Opnieuw",
            "password": "Synthetic123!",
            "confirm_password": "Synthetic123!",
        },
        follow_redirects=False,
    )
    assert repeat_response.status_code == 302
    assert repeat_response.headers["Location"].endswith("/auth/login")
    db.session.refresh(repeat_invitation)
    assert repeat_invitation.accepted_at is not None

    _login_as(auth_client, customer.id)
    portal_entry = auth_client.get("/portal", follow_redirects=False)
    assert portal_entry.status_code == 302
    assert portal_entry.headers["Location"].endswith("/portal/dashboard")

    portal_response = auth_client.get("/portal/dashboard")
    assert portal_response.status_code == 302
    profile_location = portal_response.headers["Location"]
    assert "/portal/profile/" in profile_location

    profile_response = auth_client.get(profile_location)
    assert profile_response.status_code == 200
    assert "Vul uw klantkaart aan" in profile_response.get_data(as_text=True)

    save_response = auth_client.post(
        profile_location,
        data={
            "contact_person": "Test Klant",
            "contact_email": "synthetic-customer@example.test",
            "contact_phone": "+31612345678",
            "address_street": "Teststraat",
            "address_number": "1",
            "address_postal": "1000AA",
            "address_city": "Amsterdam",
            "address_country": "Nederland",
            "vat_number": "NL123456789B01",
        },
        follow_redirects=False,
    )
    assert save_response.status_code == 302
    assert save_response.headers["Location"].endswith("/portal/dashboard?profile_saved=1")

    portal_response = auth_client.get("/portal/dashboard")
    assert portal_response.status_code == 200
    assert "Beveiligde klantomgeving" in portal_response.get_data(as_text=True)

    request_response = auth_client.post(
        "/portal/new-request",
        data={
            "request_type": "person",
            "question": "Synthetische aanvullende aanvraag",
            "context": "Alleen testdata.",
        },
        follow_redirects=False,
    )
    assert request_response.status_code == 302
    intake = PortalRequest.query.filter_by(
        submitted_by_id=customer.id,
        question="Synthetische aanvullende aanvraag",
    ).one()
    assert request_response.headers["Location"].endswith(
        f"/portal/dashboard?submitted={intake.id}"
    )
    confirmation = auth_client.get(request_response.headers["Location"])
    assert confirmation.status_code == 200
    assert f"Referentie: {intake.id[:8]}" in confirmation.get_data(as_text=True)
    assert "Ontvangen" in confirmation.get_data(as_text=True)
    duplicate_response = auth_client.post(
        "/portal/new-request",
        data={
            "request_type": "person",
            "question": "  Synthetische   aanvullende aanvraag ",
            "context": "Tweede synthetische testaanvraag.",
        },
        follow_redirects=False,
    )
    assert duplicate_response.status_code == 302
    duplicate = PortalRequest.query.filter_by(
        submitted_by_id=customer.id,
        context="Tweede synthetische testaanvraag.",
    ).one()
    assert duplicate.duplicate_of_id == intake.id
    message_page = auth_client.get(f"/portal/requests/{intake.id}/message")
    assert message_page.status_code == 200
    message_response = auth_client.post(
        f"/portal/requests/{intake.id}/message",
        data={
            "message": "Extra synthetische informatie.",
            "file": (BytesIO(b"%PDF-1.7\nsynthetic attachment"), "context.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert message_response.status_code == 302
    assert PortalRequestMessage.query.filter_by(
        request_id=intake.id,
        author_user_id=customer.id,
    ).one().message == "Extra synthetische informatie."
    db.session.refresh(intake)
    assert intake.status == "additional_info"
    attachment = Document.query.filter_by(portal_request_id=intake.id).one()
    assert attachment.original_filename == "context.pdf"
    download_response = auth_client.get(
        f"/portal/requests/{intake.id}/documents/{attachment.id}/download"
    )
    assert download_response.status_code == 200
    assert b"synthetic attachment" in download_response.data

    cms_response = auth_client.get("/cms/dashboard", follow_redirects=False)
    assert cms_response.status_code == 302
    assert "/portal/dashboard" in cms_response.headers["Location"]


def test_portal_customer_can_access_released_content_and_not_other_tenant(
    app, client, db_session, monkeypatch
):
    """Released portal content is visible only to its own customer tenant."""
    tenant = Tenant(
        name="Synthetische Portal Klant",
        slug="synthetic-portal-customer",
        workspace_type="full",
        subscription_status="trialing",
        join_code="SYN-PORTAL-1",
    )
    customer = User(
        username="synthetic-portal-user",
        email="synthetic-portal@example.test",
        full_name="Synthetic Portal User",
        role="portal_admin",
        is_active=True,
        is_super_admin=False,
    )
    customer.set_password("Synthetic123!")
    tenant.users.append(customer)
    db_session.add(tenant)
    db_session.flush()

    portal_client = Client(tenant_id=tenant.id, name="Synthetic Portal Client")
    db_session.add(portal_client)
    db_session.flush()
    db_session.add(
        ClientPortalAccess(
            tenant_id=tenant.id,
            client_id=portal_client.id,
            user_id=customer.id,
            email=customer.email,
            role="portal_viewer",
            is_active=True,
        )
    )

    case = Case(
        tenant_id=tenant.id,
        case_number="SYN-0001",
        client_id=portal_client.id,
        title="Synthetic released case",
        start_date=date.today(),
        status="closed",
        pv_body="# Released report\n\nSynthetic report body.",
        report_status="released",
    )
    staff = User(
        username="synthetic-case-handler",
        email="synthetic-case-handler@example.test",
        full_name="Synthetic Case Handler",
        role="investigator",
        is_active=True,
        is_super_admin=False,
    )
    staff.set_password("Synthetic123!")
    tenant.users.append(staff)
    db_session.add(case)
    db_session.flush()
    case.lead_investigator_id = staff.id
    db_session.commit()

    relative_path = f"uploads/cases/{case.id}/synthetic.txt"
    file_path = Path(app.root_path) / "static" / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("synthetic document", encoding="utf-8")
    document = Document(
        tenant_id=tenant.id,
        case_id=case.id,
        filename="synthetic.txt",
        original_filename="synthetic.txt",
        storage_path=relative_path,
        mime_type="text/plain",
        file_size=len("synthetic document"),
        classification="public",
        document_type="report",
        description="Published synthetic document",
    )
    db_session.add(document)

    invoice = Invoice(
        tenant_id=tenant.id,
        invoice_number="SYN-2026-0001",
        client_id=portal_client.id,
        case_id=case.id,
        issue_date=date.today(),
        due_date=date.today(),
        status="sent",
    )
    db_session.add(invoice)
    db_session.flush()
    item = InvoiceItem(
        tenant_id=tenant.id,
        invoice_id=invoice.id,
        description="Synthetic investigation service",
        quantity=1,
        unit_price=100,
        vat_rate=21,
    )
    item.recalculate()
    db_session.add(item)
    db_session.flush()
    invoice.recalculate()
    db_session.commit()

    _login_as(client, customer.id)
    report_response = client.get(f"/portal/cases/{case.id}/report")
    assert report_response.status_code == 200
    assert "Synthetic report body" in report_response.get_data(as_text=True)
    report_download = client.get(f"/portal/cases/{case.id}/report/download")
    assert report_download.status_code == 200
    assert report_download.mimetype == "text/html"
    assert "attachment" in report_download.headers["Content-Disposition"]
    assert "Synthetic report body" in report_download.get_data(as_text=True)
    assert AuditLog.query.filter_by(
        action="portal.report.downloaded", entity_id=case.id, user_id=customer.id
    ).count() == 1

    documents_response = client.get(f"/portal/cases/{case.id}/documents")
    assert documents_response.status_code == 200
    assert "synthetic.txt" in documents_response.get_data(as_text=True)

    download_response = client.get(f"/portal/documents/{document.id}/download")
    assert download_response.status_code == 200
    assert download_response.data == b"synthetic document"

    invoice_response = client.get(f"/portal/invoices/{invoice.id}")
    assert invoice_response.status_code == 200
    assert "SYN-2026-0001" in invoice_response.get_data(as_text=True)

    monkeypatch.setattr("cms.routes.case_messages._send", lambda *args, **kwargs: None)
    customer_message = client.post(
        f"/portal/cases/{case.id}/messages",
        data={"message": "Een synthetische klantvraag."},
        follow_redirects=False,
    )
    assert customer_message.status_code == 302
    assert CaseMessage.query.filter_by(
        case_id=case.id, sender_user_id=customer.id, sender_role="customer",
        message="Een synthetische klantvraag.",
    ).count() == 1
    staff_notification = Notification.query.filter_by(
        user_id=staff.id, title="Nieuw klantbericht"
    ).one()
    assert staff_notification.is_read is False

    _login_as(client, staff.id)
    staff_page = client.get(f"/cms/workflow/case/{case.id}/messages")
    assert staff_page.status_code == 200
    assert "Een synthetische klantvraag." in staff_page.get_data(as_text=True)
    db_session.refresh(staff_notification)
    assert staff_notification.is_read is True
    staff_message = client.post(
        f"/cms/workflow/case/{case.id}/messages",
        data={"message": "Een synthetisch antwoord van de behandelaar."},
        follow_redirects=False,
    )
    assert staff_message.status_code == 302
    assert CaseMessage.query.filter_by(
        case_id=case.id, sender_user_id=staff.id, sender_role="staff",
        message="Een synthetisch antwoord van de behandelaar.",
    ).count() == 1

    _login_as(client, customer.id)
    customer_page = client.get(f"/portal/cases/{case.id}/messages")
    assert customer_page.status_code == 200
    assert "Een synthetisch antwoord van de behandelaar." in customer_page.get_data(as_text=True)
    customer_notification = Notification.query.filter_by(
        user_id=customer.id, title="Nieuw bericht van Iveras"
    ).one()
    assert customer_notification.is_read is True
    assert AuditLog.query.filter_by(
        action="case.message.sent", case_id=case.id
    ).count() == 2

    other_tenant = Tenant(
        name="Andere Synthetische Tenant",
        slug="other-synthetic-tenant",
        workspace_type="portal_only",
        subscription_status="trialing",
        join_code="SYN-OTHER-1",
    )
    other_user = User(
        username="other-portal-user",
        email="other-portal@example.test",
        full_name="Other Portal User",
        role="portal_admin",
        is_active=True,
        is_super_admin=False,
    )
    other_user.set_password("Synthetic123!")
    other_tenant.users.append(other_user)
    db_session.add(other_tenant)
    db_session.commit()

    _login_as(client, other_user.id)
    assert client.get(f"/portal/cases/{case.id}/report").status_code == 404
    assert client.get(f"/portal/cases/{case.id}/report/download").status_code == 404
    assert client.get(f"/portal/cases/{case.id}/documents").status_code == 404
    assert client.get(f"/portal/documents/{document.id}/download").status_code == 404
    assert client.get(f"/portal/invoices/{invoice.id}").status_code == 404


def test_internal_review_converts_request_and_links_intake_documents(
    auth_client, db_session
):
    """Conversion preserves intake context and makes attachments case-visible."""
    reviewer = User.query.filter_by(username="admin").one()
    portal_client = Client(
        tenant_id=reviewer.tenant_id,
        name="Synthetic Intake Client",
        contact_email="intake-client@example.test",
    )
    db.session.add(portal_client)
    db.session.flush()
    intake = PortalRequest(
        tenant_id=reviewer.tenant_id,
        submitted_by_id=reviewer.id,
        request_type="person",
        question="Onderzoek synthetische intake",
        context="Alleen testcontext.",
        deadline="Binnen twee weken",
    )
    db.session.add(intake)
    db.session.flush()
    document = Document(
        tenant_id=reviewer.tenant_id,
        portal_request_id=intake.id,
        filename="intake-context.pdf",
        original_filename="intake-context.pdf",
        mime_type="application/pdf",
        file_size=12,
        storage_path="static/uploads/portal-requests/synthetic/intake-context.pdf",
        classification="confidential",
        uploaded_by=reviewer.id,
    )
    db.session.add(document)
    db.session.commit()

    response = auth_client.post(
        "/cms/portal/requests",
        data={
            "request_id": intake.id,
            "action": "convert",
            "client_id": portal_client.id,
            "title": "Synthetische intakezaak",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    db.session.refresh(intake)
    db.session.refresh(document)
    assert intake.status == "converted"
    assert intake.client_id == portal_client.id
    assert intake.case_id is not None
    assert document.case_id == intake.case_id
    assert "Onderzoek synthetische intake" in intake.case.description
    assert "Alleen testcontext." in intake.case.description
    assert "Binnen twee weken" in intake.case.description
    notification = Notification.query.filter_by(
        user_id=reviewer.id, category="portal", title="Uw aanvraag is omgezet naar een zaak"
    ).one()
    mark_read = auth_client.post(
        f"/portal/notifications/{notification.id}/read", follow_redirects=False
    )
    assert mark_read.status_code == 302
    db.session.refresh(notification)
    assert notification.is_read is True


def test_public_registration_rate_limit_blocks_repeated_submissions(client):
    """Public intake must stop automated repeated submissions per source IP."""
    from cms.rate_limiting import _api_rate_limits

    _api_rate_limits.clear()
    try:
        responses = [
            client.post(
                "/portal/register",
                data={
                    "organization_name": "Synthetic Rate Limit Test",
                    "contact_name": "Synthetic User",
                    "email": f"rate-limit-{index}@example.test",
                    "registration_type": "other",
                    "message": "Synthetische vraag voor rate-limit-test.",
                },
            )
            for index in range(6)
        ]
        assert [response.status_code for response in responses[:5]] == [200] * 5
        assert responses[5].status_code == 429
        assert responses[5].get_json()["error"] == "Rate limit exceeded"
        assert responses[5].headers["Retry-After"]
    finally:
        _api_rate_limits.clear()


def test_public_registration_scenarios_apply_distinct_required_fields(client):
    private_response = client.post(
        "/portal/register",
        data={
            "registration_type": "one_off",
            "contact_name": "Particuliere aanvrager",
            "email": "private-scenario@example.test",
            "message": "Ik wil een eenmalig OSINT-onderzoek aanvragen.",
        },
    )
    assert private_response.status_code == 200
    private_registration = CustomerRegistrationRequest.query.filter_by(
        email="private-scenario@example.test"
    ).one()
    assert private_registration.organization_name == "Particuliere aanvrager"

    organization_response = client.post(
        "/portal/register",
        data={
            "registration_type": "organization",
            "contact_name": "Organisatiecontact",
            "email": "organization-scenario@example.test",
        },
    )
    assert organization_response.status_code == 400
    assert b"organisatienaam" in organization_response.data

    other_response = client.post(
        "/portal/register",
        data={
            "registration_type": "other",
            "contact_name": "Andere vraag",
            "email": "other-scenario@example.test",
        },
    )
    assert other_response.status_code == 400
    assert b"onderzoeksvraag" in other_response.data


def test_internal_reviewer_can_accept_request_and_customer_sees_status(
    auth_client, db_session
):
    reviewer = User.query.filter_by(username="admin").one()
    portal_client = Client(
        tenant_id=reviewer.tenant_id,
        name="Synthetic Accepted Client",
    )
    db_session.add(portal_client)
    db_session.flush()
    intake = PortalRequest(
        tenant_id=reviewer.tenant_id,
        submitted_by_id=reviewer.id,
        client_id=portal_client.id,
        request_type="other",
        question="Synthetische geaccepteerde aanvraag",
        status="submitted",
    )
    db_session.add(intake)
    db_session.commit()

    response = auth_client.post(
        "/cms/portal/requests",
        data={"request_id": intake.id, "action": "accept"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    db_session.refresh(intake)
    assert intake.status == "accepted"
    assert Notification.query.filter_by(
        title="Uw aanvraag is geaccepteerd"
    ).count() == 1


def test_intake_admin_can_assign_and_route_request_without_general_case_role(
    app, client, db_session
):
    """The dedicated intake role can route work, but a named investigator owns the case."""
    admin = User.query.filter_by(username="admin").one()
    intake_admin = User(
        username="synthetic-intake-admin",
        email="synthetic-intake-admin@example.test",
        full_name="Synthetic Intake Admin",
        role="intake_admin",
        tenant_id=admin.tenant_id,
        is_active=True,
    )
    investigator = User(
        username="synthetic-intake-investigator",
        email="synthetic-intake-investigator@example.test",
        full_name="Synthetic Intake Investigator",
        role="investigator",
        tenant_id=admin.tenant_id,
        is_active=True,
    )
    intake_admin.set_password("Synthetic123!")
    investigator.set_password("Synthetic123!")
    portal_client = Client(tenant_id=admin.tenant_id, name="Synthetic Routed Client")
    db_session.add_all([intake_admin, investigator, portal_client])
    db_session.flush()
    intake = PortalRequest(
        tenant_id=admin.tenant_id,
        submitted_by_id=admin.id,
        request_type="person",
        question="Synthetische routeerbare aanvraag",
    )
    db_session.add(intake)
    db_session.commit()

    intake_client = client
    _login_as(intake_client, intake_admin.id)
    assert intake_client.get("/cms/portal/requests").status_code == 200

    assign_response = intake_client.post(
        "/cms/portal/requests",
        data={
            "request_id": intake.id,
            "action": "assign",
            "assigned_to_id": investigator.id,
        },
        follow_redirects=False,
    )
    assert assign_response.status_code == 302
    db_session.refresh(intake)
    assert intake.assigned_to_id == investigator.id
    assignment_notification = Notification.query.filter_by(
        user_id=investigator.id, title="Portal-aanvraag aan u toegewezen"
    ).one()
    assert assignment_notification.link.endswith("/cms/portal/requests")

    convert_response = intake_client.post(
        "/cms/portal/requests",
        data={
            "request_id": intake.id,
            "action": "convert",
            "client_id": portal_client.id,
            "title": "Synthetische gerouteerde zaak",
            "assigned_to_id": investigator.id,
        },
        follow_redirects=False,
    )
    assert convert_response.status_code == 302
    db_session.refresh(intake)
    assert intake.status == "converted"
    assert intake.case.assigned_to == investigator.id
    assert intake.case.lead_investigator_id == investigator.id
    assert Notification.query.filter_by(
        user_id=investigator.id, title="Nieuwe zaak aan u toegewezen"
    ).count() == 1
