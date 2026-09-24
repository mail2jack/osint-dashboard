"""Fast regression coverage for case-detail findings pagination."""

from datetime import UTC, datetime, timedelta
import uuid

from cms.models import Case, Client, Finding, db


def _admin():
    from cms.models import User

    return User.query.filter_by(username="admin").one()


def test_case_detail_shows_findings_in_pages(auth_client):
    admin = _admin()
    token = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    client = Client(
        tenant_id=admin.tenant_id,
        name=f"Pagination client {token}",
        is_active=True,
        created_by=admin.id,
    )
    db.session.add(client)
    db.session.flush()
    case = Case(
        tenant_id=admin.tenant_id,
        case_number=f"PAGE-{token}",
        client_id=client.id,
        title="Pagination case",
        status="open",
        priority="medium",
        start_date=now.date(),
        created_by=admin.id,
        lead_investigator_id=admin.id,
    )
    db.session.add(case)
    db.session.flush()
    for number in range(26):
        db.session.add(
            Finding(
                tenant_id=case.tenant_id,
                case_id=case.id,
                created_by=admin.id,
                title=f"Paged finding {number:02d}",
                content="pagination fixture",
                source_type="test",
                created_at=now + timedelta(seconds=number),
            )
        )
    db.session.commit()

    first = auth_client.get(f"/cms/workflow/case/{case.id}")
    body = first.get_data(as_text=True)
    assert first.status_code == 200
    assert "findings_page=2" in body
    assert "Paged finding 00" not in body
    assert "Paged finding 25" in body

    second = auth_client.get(f"/cms/workflow/case/{case.id}?findings_page=2")
    body = second.get_data(as_text=True)
    assert second.status_code == 200
    assert "findings_page=1" in body
    assert "Paged finding 00" in body
    assert "Paged finding 25" not in body
