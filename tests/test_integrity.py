"""Tests for the IntegrityStamp feature (content_hash on Finding)."""

from datetime import datetime, timezone


class TestIntegrityStamp:
    """Test that Finding content_hash is auto-computed and verifiable."""

    def _create_finding(self, app):
        from cms import db
        from cms.models import Client, Case, Subject, Finding

        client = Client(
            name="Integrity Client",
            contact_person="Test",
            contact_email="test@test.nl",
            is_active=True,
        )
        db.session.add(client)
        db.session.flush()
        case = Case(
            case_number="C-INT-001",
            client_id=client.id,
            title="Integrity Test Case",
            status="open",
            priority="medium",
            start_date=datetime.now(timezone.utc).date(),
        )
        db.session.add(case)
        db.session.flush()
        subject = Subject(name="Integrity Subject", subject_type="person")
        db.session.add(subject)
        db.session.flush()

        finding = Finding(
            case_id=case.id,
            subject_id=subject.id,
            title="Test finding for integrity",
            content="This is the finding content",
            source_url="https://example.com/profile",
            source_type="osint",
            created_by="test-user-id",
            created_at=datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        )
        db.session.add(finding)
        db.session.commit()
        return finding

    def test_hash_auto_computed_on_create(self, app):
        finding = self._create_finding(app)
        assert finding.content_hash is not None
        assert len(finding.content_hash) == 64  # SHA-256 hex digest

    def test_hash_is_deterministic(self, app):
        from cms.models import Finding

        h1 = Finding._compute_content_hash(
            title="Test",
            content="Content",
            source_url="https://example.com",
            source_type="osint",
            raw_data=None,
            created_by="user-1",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        h2 = Finding._compute_content_hash(
            title="Test",
            content="Content",
            source_url="https://example.com",
            source_type="osint",
            raw_data=None,
            created_by="user-1",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert h1 == h2

    def test_hash_differs_for_different_content(self, app):
        from cms.models import Finding

        h1 = Finding._compute_content_hash(
            title="Title A", content="Content A", created_by="u1"
        )
        h2 = Finding._compute_content_hash(
            title="Title A", content="Content B", created_by="u1"
        )
        assert h1 != h2

    def test_hash_differs_for_different_title(self, app):
        from cms.models import Finding

        h1 = Finding._compute_content_hash(
            title="Title A", content="Content", created_by="u1"
        )
        h2 = Finding._compute_content_hash(
            title="Title B", content="Content", created_by="u1"
        )
        assert h1 != h2

    def test_hash_differs_for_different_raw_data(self, app):
        from cms.models import Finding

        h1 = Finding._compute_content_hash(
            title="T", content="C", raw_data={"key": "value1"}, created_by="u1"
        )
        h2 = Finding._compute_content_hash(
            title="T", content="C", raw_data={"key": "value2"}, created_by="u1"
        )
        assert h1 != h2

    def test_verify_integrity_passes_for_unchanged_finding(self, app):
        finding = self._create_finding(app)
        assert finding.verify_integrity() is True

    def test_verify_integrity_fails_after_content_change(self, app):
        from cms import db

        finding = self._create_finding(app)
        assert finding.verify_integrity() is True

        finding.content = "Modified content — tampered!"
        db.session.commit()
        assert finding.verify_integrity() is False

    def test_verify_integrity_fails_after_title_change(self, app):
        from cms import db

        finding = self._create_finding(app)
        finding.title = "Modified title"
        db.session.commit()
        assert finding.verify_integrity() is False

    def test_verify_integrity_fails_after_raw_data_change(self, app):
        from cms import db

        finding = self._create_finding(app)
        finding.raw_data = {"new": "data"}
        db.session.commit()
        assert finding.verify_integrity() is False

    def test_verify_integrity_returns_false_when_no_hash(self, app):
        from cms import db

        finding = self._create_finding(app)
        finding.content_hash = None
        db.session.commit()
        assert finding.verify_integrity() is False

    def test_to_dict_includes_content_hash(self, app):
        finding = self._create_finding(app)
        d = finding.to_dict()
        assert "content_hash" in d
        assert d["content_hash"] == finding.content_hash

    def test_to_dict_includes_integrity_verified(self, app):
        finding = self._create_finding(app)
        d = finding.to_dict()
        assert "integrity_verified" in d
        assert d["integrity_verified"] is True

    def test_to_dict_integrity_verified_false_when_tampered(self, app):
        from cms import db

        finding = self._create_finding(app)
        finding.content = "tampered"
        db.session.commit()
        d = finding.to_dict()
        assert d["integrity_verified"] is False

    def test_to_dict_integrity_verified_none_when_no_hash(self, app):
        from cms import db

        finding = self._create_finding(app)
        finding.content_hash = None
        db.session.commit()
        d = finding.to_dict()
        assert d["integrity_verified"] is None

    def test_hash_includes_source_url(self, app):
        from cms.models import Finding

        h1 = Finding._compute_content_hash(
            title="T", content="C", source_url="https://a.com", created_by="u"
        )
        h2 = Finding._compute_content_hash(
            title="T", content="C", source_url="https://b.com", created_by="u"
        )
        assert h1 != h2

    def test_hash_includes_created_by(self, app):
        from cms.models import Finding

        h1 = Finding._compute_content_hash(title="T", content="C", created_by="user-1")
        h2 = Finding._compute_content_hash(title="T", content="C", created_by="user-2")
        assert h1 != h2

    def test_hash_includes_created_at(self, app):
        from cms.models import Finding

        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
        h1 = Finding._compute_content_hash(title="T", content="C", created_at=t1)
        h2 = Finding._compute_content_hash(title="T", content="C", created_at=t2)
        assert h1 != h2

    def test_compute_hash_matches_stored_hash(self, app):
        finding = self._create_finding(app)
        assert finding.compute_hash() == finding.content_hash
