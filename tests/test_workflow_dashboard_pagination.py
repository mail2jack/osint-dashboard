"""Regression coverage for the paginated, access-scoped workflow dashboard."""

from datetime import UTC, datetime, timedelta
import uuid

from cms.models import Case, Client, User, UserRole, db


def _admin():
    return User.query.filter_by(username="admin").one()


def _make_case(admin, client, token, number, created_at, **kwargs):
    case = Case(
        tenant_id=admin.tenant_id,
        case_number=f"DASH-{token}-{number:02d}",
        client_id=client.id,
        title=f"Dashboard case {number:02d}",
        status="open",
        priority="medium",
        start_date=created_at.date(),
        created_at=created_at,
        created_by=kwargs.get("created_by", admin.id),
        lead_investigator_id=kwargs.get("lead_investigator_id", admin.id),
    )
    db.session.add(case)
    return case


def test_dashboard_paginates_cases_deterministically(auth_client):
    admin = _admin()
    token = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    client = Client(
        tenant_id=admin.tenant_id,
        name=f"Dashboard pagination client {token}",
        is_active=True,
        created_by=admin.id,
    )
    db.session.add(client)
    db.session.flush()
    for number in range(26):
        _make_case(admin, client, token, number, now + timedelta(seconds=number))
    db.session.commit()

    first = auth_client.get("/cms/workflow/?page=1")
    first_body = first.get_data(as_text=True)
    assert first.status_code == 200
    assert "Dashboard case 25" in first_body
    assert "Dashboard case 00" not in first_body
    assert "page=2" in first_body
    assert "Page 1 / 2" in first_body

    second = auth_client.get("/cms/workflow/?page=2")
    second_body = second.get_data(as_text=True)
    assert second.status_code == 200
    assert "Dashboard case 00" in second_body
    assert "Dashboard case 25" not in second_body
    assert "page=1" in second_body


def test_dashboard_hides_unassigned_case_from_investigator(app):
    admin = _admin()
    token = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    investigator = User(
        tenant_id=admin.tenant_id,
        username=f"dashboard-investigator-{token}",
        email=f"dashboard-investigator-{token}@example.test",
        full_name="Dashboard Investigator",
        role=UserRole.INVESTIGATOR.value,
        is_active=True,
    )
    investigator.set_password("Test1234!")
    client = Client(
        tenant_id=admin.tenant_id,
        name=f"Dashboard access client {token}",
        is_active=True,
        created_by=admin.id,
    )
    db.session.add_all([investigator, client])
    db.session.flush()
    allowed = _make_case(
        admin, client, token, 1, now, lead_investigator_id=investigator.id
    )
    denied = _make_case(admin, client, token, 2, now + timedelta(seconds=1))
    db.session.commit()

    browser = app.test_client()
    with browser.session_transaction() as session:
        session["_user_id"] = str(investigator.id)
        session["_fresh"] = True
    response = browser.get("/cms/workflow/")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert allowed.title in body
    assert denied.title not in body
