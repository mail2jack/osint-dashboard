"""Regression tests for the investigator-facing report entry point."""

import uuid
from datetime import UTC, datetime

from cms.models import Case, Client, DocumentTemplate, Subject, Tenant, User, db


def _admin() -> User:
    return User.query.filter_by(username="admin").one()


def _case_for_admin() -> Case:
    admin = _admin()
    client = Client(
        tenant_id=admin.tenant_id,
        name="Report hub client",
        is_active=True,
        created_by=admin.id,
    )
    db.session.add(client)
    db.session.flush()
    case = Case(
        tenant_id=admin.tenant_id,
        case_number="REPORT-HUB-001",
        client_id=client.id,
        title="Report hub case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
        created_by=admin.id,
        lead_investigator_id=admin.id,
    )
    db.session.add(case)
    db.session.commit()
    return case


def _isolated_case_without_templates(client):
    """Return a signed-in writer in a tenant with no pre-existing templates."""
    token = uuid.uuid4().hex[:8]
    tenant = Tenant(
        name=f"Report hub {token}",
        slug=f"report-hub-{token}",
        tier="enterprise",
        join_code=uuid.uuid4().hex[:12].upper(),
        is_active=True,
    )
    db.session.add(tenant)
    db.session.flush()
    writer = User(
        username=f"report_hub_{token}",
        email=f"report_hub_{token}@example.test",
        full_name="Report Hub Writer",
        tenant_id=tenant.id,
        role="admin",
        is_active=True,
    )
    writer.set_password("Test1234!")
    db.session.add(writer)
    db.session.flush()
    customer = Client(
        tenant_id=tenant.id,
        name="Isolated report hub client",
        is_active=True,
        created_by=writer.id,
    )
    db.session.add(customer)
    db.session.flush()
    case = Case(
        tenant_id=tenant.id,
        case_number=f"REPORT-{token}",
        client_id=customer.id,
        title="Isolated report hub case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
        created_by=writer.id,
        lead_investigator_id=writer.id,
    )
    db.session.add(case)
    db.session.commit()
    with client.session_transaction() as session:
        session["_user_id"] = writer.id
        session["_fresh"] = True
    return case


def test_report_hub_never_requires_a_template(client):
    case = _isolated_case_without_templates(client)

    response = client.get(f"/cms/workflow/case/{case.id}/report")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert f"/cms/workflow/case/{case.id}/pv" in body
    assert f"/cms/cases/{case.id}/report" in body
    assert f"/cms/cases/{case.id}/report-pdf" in body
    # The app's locale can be selected by a preceding request in the shared
    # test process.  Assert the stable report controls, not English copy.
    assert "Create a document template" in body or "Documentsjabloon maken" in body
    assert "/cms/templates/create" in body


def test_report_hub_lists_tenant_template_and_preselects_it(auth_client):
    case = _case_for_admin()
    template = DocumentTemplate(
        tenant_id=case.tenant_id,
        name="Investigation report v1",
        description="Formal QA layout",
        template_type="report",
        content="{{ case.case_number }}",
        is_active=True,
        is_default=True,
        created_by=_admin().id,
    )
    db.session.add(template)
    db.session.commit()

    hub = auth_client.get(f"/cms/workflow/case/{case.id}/report")

    assert hub.status_code == 200
    assert "Investigation report v1" in hub.get_data(as_text=True)
    generator = auth_client.get(
        f"/cms/cases/{case.id}/generate-report?template_id={template.id}"
    )
    assert generator.status_code == 200
    assert f'<option value="{template.id}" selected>' in generator.get_data(
        as_text=True
    )


def test_report_hub_rejects_non_investigator(auth_client, client):
    case = _case_for_admin()
    admin = _admin()
    viewer = User(
        username="report_hub_viewer",
        email="report_hub_viewer@example.test",
        full_name="Report Hub Viewer",
        tenant_id=admin.tenant_id,
        role="viewer",
        is_active=True,
    )
    viewer.set_password("Test1234!")
    db.session.add(viewer)
    db.session.commit()
    with client.session_transaction() as session:
        session["_user_id"] = viewer.id
        session["_fresh"] = True

    response = client.get(f"/cms/workflow/case/{case.id}/report")

    assert response.status_code == 403


def test_case_detail_links_linked_subject_to_profile(auth_client):
    case = _case_for_admin()
    subject = Subject(
        tenant_id=case.tenant_id,
        name="Profile navigation subject",
        subject_type="person",
    )
    db.session.add(subject)
    db.session.flush()
    case.subjects.append(subject)
    db.session.commit()

    response = auth_client.get(f"/cms/workflow/case/{case.id}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert f'/cms/subjects/{subject.id}/profile' in body
    assert 'class="subject-profile-link"' in body
