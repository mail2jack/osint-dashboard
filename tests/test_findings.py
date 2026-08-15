from datetime import UTC, datetime


def _make_client_and_case():
    from cms import db
    from cms.models import Case, Client

    client = Client(
        name="Test Client",
        contact_person="Test",
        contact_email="test@test.nl",
        is_active=True,
    )
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number="C-001",
        client_id=client.id,
        title="Test Case",
        status="open",
        priority="medium",
        start_date=datetime.now(UTC).date(),
    )
    db.session.add(case)
    db.session.flush()
    db.session.commit()
    return client.id, case.id


class TestFindingsCRUD:
    """Test finding creation, reading, and management."""

    def test_create_finding_requires_auth(self, client):
        resp = client.post(
            "/cms/findings/create",
            json={
                "case_id": 1,
                "subject_id": 1,
                "title": "Test finding",
            },
        )
        assert resp.status_code in (302, 401)

    def test_create_finding_minimal(self, auth_client, app):
        from cms import db
        from cms.models import Subject

        client_id, case_id = _make_client_and_case()
        subject = Subject(name="Test Subject", subject_type="person")
        db.session.add(subject)
        db.session.commit()
        resp = auth_client.post(
            "/cms/findings/create",
            json={
                "case_id": str(case_id),
                "subject_id": str(subject.id),
                "title": "Test OSINT finding",
                "content": "Found profile on example.com",
                "source_url": "https://example.com/profile",
                "source_type": "osint",
                "finding_type": "identity",
            },
        )
        data = resp.get_json()
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {data}"
        assert data.get("message") is not None
        assert data.get("finding") is not None

    def test_create_finding_missing_title(self, auth_client, app):
        _, case_id = _make_client_and_case()
        resp = auth_client.post(
            "/cms/findings/create",
            json={
                "case_id": str(case_id),
            },
        )
        assert resp.status_code == 400

    def test_check_existing_urls(self, auth_client, app):
        from cms import db
        from cms.models import Case, Client, Finding, Subject

        client = Client(
            name="Test Client",
            contact_person="Test",
            contact_email="test@test.nl",
            is_active=True,
        )
        db.session.add(client)
        db.session.flush()
        case = Case(
            case_number="C-001",
            client_id=client.id,
            title="Test Case",
            status="open",
            priority="medium",
            start_date=datetime.now(UTC).date(),
        )
        db.session.add(case)
        db.session.flush()
        subject = Subject(name="Test Subject", subject_type="person")
        db.session.add(subject)
        db.session.flush()
        finding = Finding(
            case_id=case.id,
            subject_id=subject.id,
            title="Existing finding",
            content="Existing",
            source_url="https://example.com/existing",
            source_type="osint",
            finding_type="identity",
            created_by=1,
        )
        db.session.add(finding)
        db.session.commit()

        resp = auth_client.post(
            "/cms/api/findings/check-existing-urls",
            json={
                "case_id": str(case.id),
                "urls": ["https://example.com/existing", "https://example.com/new"],
            },
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get("existing") is not None
        assert "https://example.com/existing" in data["existing"]
        assert "https://example.com/new" not in data["existing"]


class TestSocialFindings:
    """Test social account and finding integration."""

    def test_save_username_findings(self, auth_client, app):
        from cms import db
        from cms.models import Case, Subject

        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        subject = Subject(name="testuser", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()

        resp = auth_client.post(
            f"/cms/api/subjects/{subject.id}/save-username-findings",
            json={
                "case_id": str(case_id),
                "results": [
                    {
                        "url": "https://github.com/testuser",
                        "platform": "GitHub",
                        "username": "testuser",
                    },
                    {
                        "url": "https://twitter.com/testuser",
                        "platform": "Twitter",
                        "username": "testuser",
                    },
                ],
            },
        )
        data = resp.get_json()
        assert resp.status_code == 201
        assert data["findings_count"] == 2

    def test_save_username_findings_no_duplicate_social(self, auth_client, app):
        from cms import db
        from cms.models import Case, SocialAccount, Subject

        _, case_id = _make_client_and_case()
        case = db.session.get(Case, case_id)
        subject = Subject(name="testuser", subject_type="person")
        db.session.add(subject)
        case.subjects.append(subject)
        db.session.commit()

        resp = auth_client.post(
            f"/cms/api/subjects/{subject.id}/save-username-findings",
            json={
                "case_id": str(case_id),
                "results": [
                    {
                        "url": "https://github.com/testuser",
                        "platform": "GitHub",
                        "username": "testuser",
                    },
                ],
            },
        )
        assert resp.status_code == 201
        count1 = SocialAccount.query.filter_by(subject_id=subject.id).count()

        resp = auth_client.post(
            f"/cms/api/subjects/{subject.id}/save-username-findings",
            json={
                "case_id": str(case_id),
                "results": [
                    {
                        "url": "https://github.com/testuser",
                        "platform": "GitHub",
                        "username": "testuser",
                    },
                ],
            },
        )
        assert resp.status_code == 201
        count2 = SocialAccount.query.filter_by(subject_id=subject.id).count()
        assert count2 == count1, "Should not create duplicate social accounts"

    def test_save_finding_as_social_account(self, auth_client, app):
        from cms import db
        from cms.models import Case, Client, Finding, SocialAccount, Subject

        client = Client(
            name="Test Client",
            contact_person="Test",
            contact_email="test@test.nl",
            is_active=True,
        )
        db.session.add(client)
        db.session.flush()
        case = Case(
            case_number="C-001",
            client_id=client.id,
            title="Test Case",
            status="open",
            priority="medium",
            start_date=datetime.now(UTC).date(),
        )
        db.session.add(case)
        db.session.flush()
        subject = Subject(name="Test Subject", subject_type="person")
        db.session.add(subject)
        db.session.flush()
        finding = Finding(
            case_id=case.id,
            subject_id=subject.id,
            title="Social finding",
            content="Social finding content",
            source_url="https://instagram.com/testuser",
            source_type="osint",
            finding_type="identity",
            created_by=1,
        )
        db.session.add(finding)
        db.session.commit()

        resp = auth_client.post(
            "/cms/api/findings/save-as-social-account",
            json={
                "finding_id": str(finding.id),
            },
        )
        data = resp.get_json()
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {data}"
        account = SocialAccount.query.filter_by(subject_id=subject.id).first()
        assert account is not None
