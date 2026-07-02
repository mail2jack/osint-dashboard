"""
Workflow integration tests — model columns, CRUD routes, access control, auto-invoice.
"""

from datetime import datetime, timezone

from cms.models import (
    db,
    Client,
    Case,
    Subject,
    Finding,
    ResearchAction,
    ActionFinding,
    FindingScreenshot,
    ServiceRate,
)


class TestModelsExist:
    """Verify new model tables and columns exist in the main DB."""

    def _has_table(self, app, name):
        from sqlalchemy import inspect

        return name in inspect(db.engine).get_table_names()

    def test_research_action_table(self, app):
        assert self._has_table(app, "research_actions")

    def test_action_finding_table(self, app):
        assert self._has_table(app, "action_findings")

    def test_finding_screenshot_table(self, app):
        assert self._has_table(app, "finding_screenshots")

    def test_service_rate_table(self, app):
        assert self._has_table(app, "service_rates")

    def test_client_reference_column(self, app):
        assert hasattr(Client, "reference")

    def test_case_pv_columns(self, app):
        assert hasattr(Case, "pv_body")
        assert hasattr(Case, "pv_updated_at")

    def test_subject_social_accounts(self, app):
        assert hasattr(Subject, "social_accounts")

    def test_finding_new_columns(self, app):
        assert hasattr(Finding, "icon")
        assert hasattr(Finding, "verified")
        assert hasattr(Finding, "comment")
        assert hasattr(Finding, "raw_data")
        assert hasattr(Finding, "archived_at")
        assert hasattr(Finding, "detail")


class TestServiceRatesSeed:
    def test_rates_auto_seeded(self, app, db_session):
        rates = ServiceRate.query.all()
        assert len(rates) == 3
        types = {r.service_type for r in rates}
        assert types == {"case_creation", "research_action", "pv_creation"}

    def test_case_creation_rate(self, db_session):
        rate = ServiceRate.query.filter_by(service_type="case_creation").first()
        assert rate is not None
        assert rate.unit_price == 75
        assert rate.vat_rate == 21.00


class TestWorkflowAccessControl:
    URL = "/cms/workflow/"

    def test_unauthenticated_redirect(self, client):
        resp = client.get(self.URL)
        assert resp.status_code in (302, 403)

    def test_admin_can_access(self, auth_client):
        resp = auth_client.get(self.URL)
        assert resp.status_code == 200


class TestCaseCrud:
    def test_create_case_get(self, auth_client, db_session):
        resp = auth_client.get("/cms/workflow/case/new")
        assert resp.status_code == 200

    def test_create_case_post(self, auth_client, db_session):
        resp = auth_client.post(
            "/cms/workflow/case/new",
            data={
                "client_name": "Test Client",
                "title": "Test Workflow Case",
                "subject_0_name": "Test Person",
                "subject_0_type": "person",
                "priority": "medium",
            },
        )
        assert resp.status_code in (200, 302)
        assert Case.query.filter_by(title="Test Workflow Case").first() is not None

    def test_case_detail_404(self, auth_client):
        resp = auth_client.get("/cms/workflow/case/nonexistent")
        assert resp.status_code == 404

    def test_detail_shows_created_case(self, auth_client, db_session):
        auth_client.post(
            "/cms/workflow/case/new",
            data={
                "client_name": "Client Detail",
                "title": "Detail Case",
                "subject_0_name": "Subject Detail",
                "subject_0_type": "person",
                "priority": "low",
            },
        )
        case = Case.query.filter_by(title="Detail Case").first()
        assert case is not None
        resp = auth_client.get(f"/cms/workflow/case/{case.id}")
        assert resp.status_code == 200

    def test_case_list_shows_cases(self, auth_client, db_session):
        auth_client.post(
            "/cms/workflow/case/new",
            data={
                "client_name": "List Client",
                "title": "List Case",
                "subject_0_name": "List Subject",
                "subject_0_type": "person",
            },
        )
        resp = auth_client.get("/cms/workflow/")
        assert resp.status_code == 200

    def test_subject_address_persisted(self, auth_client, db_session):
        """Verify all address fields survive the create-case round-trip."""
        auth_client.post(
            "/cms/workflow/case/new",
            data={
                "client_name": "Addr Client",
                "title": "Addr Case",
                "subject_0_name": "Addr Person",
                "subject_0_type": "person",
                "subject_0_street": "Hoofdstraat",
                "subject_0_house_number": "42",
                "subject_0_house_number_addition": "A",
                "subject_0_postal_code": "1012AB",
                "subject_0_city": "Amsterdam",
            },
        )
        case = Case.query.filter_by(title="Addr Case").first()
        assert case is not None
        subjs = list(case.subjects)
        assert len(subjs) == 1
        s = subjs[0]
        assert s.street == "Hoofdstraat"
        assert s.house_number == "42"
        assert s.house_number_addition == "A"
        assert s.postal_code == "1012AB"
        assert s.city == "Amsterdam"
        assert s.address == "Hoofdstraat 42 A, 1012AB Amsterdam"


class TestAutoInvoice:
    def test_case_creation_creates_draft_invoice(self, auth_client, db_session):
        from cms.models import Invoice

        auth_client.post(
            "/cms/workflow/case/new",
            data={
                "client_name": "Invoice Client",
                "title": "Invoice Test Case",
                "subject_0_name": "Invoice Subject",
                "subject_0_type": "person",
                "priority": "high",
            },
        )
        invoices = Invoice.query.all()
        assert len(invoices) == 1
        assert invoices[0].status == "draft"
        items = invoices[0].items.all()
        assert len(items) == 1
        assert items[0].unit_price == 75


class TestResearchAction:
    def test_create_action(self, auth_client, db_session):
        auth_client.post(
            "/cms/workflow/case/new",
            data={
                "client_name": "Action Client",
                "title": "Action Case",
                "subject_0_name": "Action Subject",
                "subject_0_type": "person",
            },
        )
        case = Case.query.filter_by(title="Action Case").first()
        action = ResearchAction(
            id="test-action-id",
            case_id=case.id,
            action_type="ddg_search",
            label="Test search",
            data_value="test query",
            status="pending",
        )
        db.session.add(action)
        db.session.commit()

        fetched = ResearchAction.query.get("test-action-id")
        assert fetched is not None
        assert fetched.action_type == "ddg_search"
        assert fetched.case_id == case.id

    def test_action_finding_link(self, app, db_session):
        from cms.models import User

        admin = User.query.filter_by(username="admin").first()
        client = Client(name="AF Client")
        db.session.add(client)
        db.session.flush()
        case = Case(
            case_number="AF-TEST",
            client_id=client.id,
            title="Action Finding Test",
            status="open",
            priority="medium",
            start_date=datetime.now(timezone.utc).date(),
        )
        db.session.add(case)
        db.session.flush()

        finding = Finding(
            case_id=case.id,
            title="Linked Finding",
            content="test",
            source_type="manual",
            created_by=admin.id,
        )
        db.session.add(finding)
        db.session.flush()

        action = ResearchAction(
            id="linking-test",
            case_id=case.id,
            action_type="ddg_search",
            label="Linking Test",
            status="completed",
        )
        db.session.add(action)
        db.session.flush()

        link = ActionFinding(action_id=action.id, finding_id=finding.id)
        db.session.add(link)
        db.session.commit()

        assert finding in action.findings
        assert action in finding.research_actions


class TestFindingScreenshot:
    def test_create_screenshot(self, app, db_session):
        from cms.models import User

        admin = User.query.filter_by(username="admin").first()
        client = Client(name="SS Client")
        db.session.add(client)
        db.session.flush()
        case = Case(
            case_number="SS-TEST",
            client_id=client.id,
            title="Screenshot Test",
            status="open",
            priority="medium",
            start_date=datetime.now(timezone.utc).date(),
        )
        db.session.add(case)
        db.session.flush()

        finding = Finding(
            case_id=case.id,
            title="SS Finding",
            content="test",
            source_type="manual",
            created_by=admin.id,
        )
        db.session.add(finding)
        db.session.flush()

        ss = FindingScreenshot(
            id="test-ss-id",
            finding_id=finding.id,
            url="https://example.com/screenshot.png",
            file_path="screenshots/test.png",
        )
        db.session.add(ss)
        db.session.commit()

        assert len(finding.finding_screenshots) == 1
        assert finding.finding_screenshots[0].file_path == "screenshots/test.png"


class TestEmailCheckPGP:
    """_email_check adds PGP + Brave context findings."""

    def _make_action(self, db_session, email="test@example.com"):
        from cms.models import User, Client, Case, Subject, ResearchAction

        admin = User.query.filter_by(username="admin").first()
        client = Client(name="PGP Client")
        db.session.add(client)
        db.session.flush()
        case = Case(
            case_number="PGP-TEST",
            client_id=client.id,
            title="PGP Test Case",
            status="open",
            priority="medium",
            start_date=datetime.now(timezone.utc).date(),
        )
        db.session.add(case)
        db.session.flush()
        subject = Subject(
            name="Test Subject",
            subject_type="person",
            email=email,
        )
        db.session.add(subject)
        db.session.flush()
        case.subjects.append(subject)
        db.session.commit()
        action = ResearchAction(
            case_id=case.id,
            action_type="email",
            data_value=email,
            label="PGP Test",
            status="pending",
        )
        db.session.add(action)
        db.session.commit()
        return action

    def test_pgp_finding_added(self, app, db_session, monkeypatch):
        email = "pgp-user@example.com"
        action = self._make_action(db_session, email)

        monkeypatch.setattr(
            "cms.email_search.lookup_email",
            lambda e: {"account_checks": [], "from_cache": False},
        )

        class MockPGPResponse:
            status_code = 200
            text = "-----BEGIN PGP PUBLIC KEY BLOCK-----"

            def json(self):
                return {}

        def mock_get(url, *a, **kw):
            if "keys.openpgp.org" in url:
                return MockPGPResponse()
            raise Exception(f"Unexpected PGP request to {url}")

        import curl_cffi.requests as curl_req

        monkeypatch.setattr(curl_req, "get", mock_get)
        from cms.workflow.research import _email_check

        findings = _email_check(action)
        pgp_findings = [f for f in findings if f.get("source_type") == "pgp"]
        assert len(pgp_findings) == 1
        assert "PGP-sleutel gevonden" in pgp_findings[0]["title"]
        assert pgp_findings[0]["verified"] is True

    def test_pgp_404_no_finding(self, app, db_session, monkeypatch):
        email = "no-pgp@example.com"
        action = self._make_action(db_session, email)

        monkeypatch.setattr(
            "cms.email_search.lookup_email",
            lambda e: {"account_checks": [], "from_cache": False},
        )

        class MockPGP404:
            status_code = 404

            def json(self):
                return {}

        def mock_get(url, *a, **kw):
            if "keys.openpgp.org" in url:
                return MockPGP404()
            raise Exception(f"Unexpected PGP request to {url}")

        import curl_cffi.requests as curl_req

        monkeypatch.setattr(curl_req, "get", mock_get)
        from cms.workflow.research import _email_check

        findings = _email_check(action)
        pgp_findings = [f for f in findings if f.get("source_type") == "pgp"]
        assert len(pgp_findings) == 0

    def test_brave_context_findings(self, app, db_session, monkeypatch):
        email = "context@example.com"
        action = self._make_action(db_session, email)

        # Mock lookup_email to avoid real HTTP calls
        monkeypatch.setattr(
            "cms.email_search.lookup_email",
            lambda e: {"account_checks": [], "from_cache": False},
        )
        # Return brave key, but skip hibp
        monkeypatch.setattr(
            "cms.workflow.research._get_api_key",
            lambda k: "fake-brave-key" if k == "brave_api_key" else None,
        )

        # Mock PGP request to avoid real HTTP call
        class MockPGP404:
            status_code = 404

            def json(self):
                return {}

        import curl_cffi.requests as curl_req

        monkeypatch.setattr(curl_req, "get", lambda *a, **kw: MockPGP404())

        fake_results = [
            {
                "title": "Profiel op forum",
                "url": "https://forum.example.com/user",
                "description": "User profile page",
            },
            {
                "title": "Company site",
                "url": "https://company.example.com/team",
                "description": "Team page",
            },
        ]

        def fake_brave_search(*a, **kw):
            return fake_results

        monkeypatch.setattr(
            "cms.services.search_service.brave_search",
            fake_brave_search,
        )
        from cms.workflow.research import _email_check

        findings = _email_check(action)
        ctx_findings = [f for f in findings if f.get("source_type") == "email_context"]
        assert len(ctx_findings) == 2
        assert "Profiel op forum" in ctx_findings[0]["title"]


class TestSubdomainCheck:
    """_subdomain_check queries crt.sh for subdomains."""

    def _make_action(self, db_session, domain="example.com"):
        from cms.models import User, Client, Case, ResearchAction

        admin = User.query.filter_by(username="admin").first()
        client = Client(name="Sub Client")
        db.session.add(client)
        db.session.flush()
        case = Case(
            case_number="SUB-TEST",
            client_id=client.id,
            title="Subdomain Test Case",
            status="open",
            priority="medium",
            start_date=datetime.now(timezone.utc).date(),
        )
        db.session.add(case)
        db.session.flush()
        action = ResearchAction(
            case_id=case.id,
            action_type="subdomain",
            data_value=domain,
            label="Subdomain Test",
            status="pending",
        )
        db.session.add(action)
        db.session.commit()
        return action

    def test_subdomains_found(self, app, db_session, monkeypatch):
        action = self._make_action(db_session, "testbed.nl")

        fake_crtsh_json = [
            {"name_value": "www.testbed.nl\napi.testbed.nl"},
            {"name_value": "mail.testbed.nl"},
            {"name_value": "*.dev.testbed.nl"},
        ]

        class MockCrtSh:
            status_code = 200

            def json(self):
                return fake_crtsh_json

        import curl_cffi.requests as curl_req

        monkeypatch.setattr(curl_req, "get", lambda *a, **kw: MockCrtSh())
        from cms.workflow.research import _subdomain_check

        findings = _subdomain_check(action)
        sub_findings = [f for f in findings if f.get("source_type") == "subdomain"]
        assert len(sub_findings) == 1
        assert "4 subdomeinen" in sub_findings[0]["title"]
        assert "api.testbed.nl" in sub_findings[0]["detail"]
        assert "dev.testbed.nl" in sub_findings[0]["detail"]  # wildcard stripped

    def test_subdomain_no_domain(self, app, db_session):
        from cms.models import User, Client, Case, ResearchAction

        admin = User.query.filter_by(username="admin").first()
        client = Client(name="NoDom Client")
        db.session.add(client)
        db.session.flush()
        case = Case(
            case_number="NODOM-TEST",
            client_id=client.id,
            title="No Domain Test",
            status="open",
            priority="medium",
            start_date=datetime.now(timezone.utc).date(),
        )
        db.session.add(case)
        db.session.commit()
        action = ResearchAction(
            case_id=case.id,
            action_type="subdomain",
            data_value="",
            label="No Domain",
            status="pending",
        )
        db.session.add(action)
        db.session.commit()
        from cms.workflow.research import _subdomain_check

        findings = _subdomain_check(action)
        assert len(findings) == 0

    def test_subdomain_from_subject_email(self, app, db_session, monkeypatch):
        """If no data_value but subject has email, extract domain from it."""
        from cms.models import User, Client, Case, Subject, ResearchAction

        admin = User.query.filter_by(username="admin").first()
        client = Client(name="SubEmail Client")
        db.session.add(client)
        db.session.flush()
        case = Case(
            case_number="SUBEMAIL-TEST",
            client_id=client.id,
            title="Subject Email Test",
            status="open",
            priority="medium",
            start_date=datetime.now(timezone.utc).date(),
        )
        db.session.add(case)
        db.session.flush()
        subject = Subject(
            name="Email Subject",
            subject_type="person",
            email="user@mailhost.nl",
        )
        db.session.add(subject)
        db.session.flush()
        case.subjects.append(subject)
        db.session.flush()
        action = ResearchAction(
            case_id=case.id,
            action_type="subdomain",
            data_value="",
            label="From Subject",
            status="pending",
        )
        db.session.add(action)
        db.session.commit()

        class MockCrtSh:
            status_code = 200

            def json(self):
                return [{"name_value": "www.mailhost.nl"}]

        import curl_cffi.requests as curl_req

        monkeypatch.setattr(curl_req, "get", lambda *a, **kw: MockCrtSh())
        from cms.workflow.research import _subdomain_check

        findings = _subdomain_check(action)
        sub_findings = [f for f in findings if f.get("source_type") == "subdomain"]
        assert len(sub_findings) == 1
        assert "mailhost.nl" in sub_findings[0]["detail"]
