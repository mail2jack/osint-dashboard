"""
Findings register (ADR-0001 optie b) — central register + include-in-report flag.

Covers:
- Register index route exists and renders within allowed web routes.
- Retrieve findings via the register API with flag/filters/pagination.
- The report-flag endpoint flips include_in_report and is case-scoped.
- Official report routes (workflow PV, case report HTML) exclude
  include_in_report=false findings, while raw export/search stay unfiltered.
"""

from datetime import datetime, timezone

from cms.models import Case, Client, Finding, User, db
from cms.routes.templates import _build_report_context


def _orm_case(title="Register Case"):
    client = Client(name="Register Client")
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number=f"REG-{title.replace(' ', '').upper()}",
        client_id=client.id,
        title=title,
        status="open",
        priority="medium",
        start_date=datetime.now(timezone.utc).date(),
    )
    db.session.add(case)
    db.session.flush()
    return case


def _orm_finding(case, user, title, include_in_report=None):
    finding = Finding(
        case_id=case.id,
        tenant_id=case.tenant_id,
        created_by=user.id,
        title=title,
        content=f"content {title}",
        source_type="osint",
        confidence_level="medium",
        include_in_report=include_in_report,
    )
    db.session.add(finding)
    db.session.commit()
    return finding


def _admin_user():
    return User.query.filter_by(username="admin").first()


class TestRegisterPage:
    def test_register_page_renders(self, auth_client, db_session):
        case = _orm_case()
        _orm_finding(case, _admin_user(), "Visible finding")
        resp = auth_client.get("/cms/workflow/findings")
        assert resp.status_code == 200
        assert "Findings" in resp.get_data(as_text=True)
        assert "Visible finding" in resp.get_data(as_text=True)

    def test_register_empty_state(self, auth_client, db_session):
        resp = auth_client.get("/cms/workflow/findings")
        assert resp.status_code == 200

    def test_register_requires_auth(self, client, db_session):
        resp = client.get("/cms/workflow/findings")
        assert resp.status_code in (302, 403)


class TestFindingsApi:
    URL = "/cms/workflow/api/findings"

    def test_api_returns_findings(self, auth_client, db_session):
        case = _orm_case()
        _orm_finding(case, _admin_user(), "API finding")
        resp = auth_client.get(self.URL)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["finding_count"] == 1
        assert data["findings"][0]["title"] == "API finding"
        assert data["findings"][0]["case_id"] == case.id
        assert data["has_running"] is False

    def test_api_excludes_deleted(self, auth_client, db_session):
        case = _orm_case()
        _orm_finding(case, _admin_user(), "alive")
        f2 = _orm_finding(case, _admin_user(), "soft-deleted")
        f2.soft_delete()
        db.session.commit()
        data = auth_client.get(self.URL).get_json()
        titles = {f["title"] for f in data["findings"]}
        assert "alive" in titles
        assert "soft-deleted" not in titles

    def test_api_report_flag_filter(self, auth_client, db_session):
        case = _orm_case()
        _orm_finding(case, _admin_user(), "included-null")
        _orm_finding(case, _admin_user(), "included-true", include_in_report=True)
        _orm_finding(case, _admin_user(), "excluded", include_in_report=False)

        all_data = auth_client.get(self.URL).get_json()
        assert all_data["finding_count"] == 3

        in_data = auth_client.get(self.URL + "?report=in").get_json()
        titles_in = {f["title"] for f in in_data["findings"]}
        assert "included-null" in titles_in
        assert "included-true" in titles_in
        assert "excluded" not in titles_in

        out_data = auth_client.get(self.URL + "?report=out").get_json()
        assert [f["title"] for f in out_data["findings"]] == ["excluded"]
        assert out_data["findings"][0]["include_in_report"] is False

    def test_api_case_filter(self, auth_client, db_session):
        case_a = _orm_case("Case A")
        case_b = _orm_case("Case B")
        _orm_finding(case_a, _admin_user(), "in A")
        _orm_finding(case_b, _admin_user(), "in B")
        data = auth_client.get(self.URL + f"?case_id={case_a.id}").get_json()
        titles = {f["title"] for f in data["findings"]}
        assert titles == {"in A"}

    def test_api_status_filter(self, auth_client, db_session):
        case = _orm_case()
        _orm_finding(case, _admin_user(), "draft")
        verified = _orm_finding(case, _admin_user(), "verified one")
        verified.promote_to_verified(_admin_user())
        db.session.commit()
        data = auth_client.get(self.URL + "?status=verified").get_json()
        assert [f["title"] for f in data["findings"]] == ["verified one"]


class TestReportFlagEndpoint:
    def test_flag_flips_include_in_report(self, auth_client, db_session):
        case = _orm_case()
        f = _orm_finding(case, _admin_user(), "flag me", include_in_report=None)
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/findings/{f.id}/report-flag",
            json={"include_in_report": False},
        )
        assert resp.status_code == 200
        assert resp.get_json()["include_in_report"] is False
        db.session.expire_all()
        assert db.session.get(Finding, f.id).include_in_report is False

        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/findings/{f.id}/report-flag",
            json={"include_in_report": True},
        )
        assert resp.get_json()["include_in_report"] is True

    def test_flag_requires_bool(self, auth_client, db_session):
        case = _orm_case()
        f = _orm_finding(case, _admin_user(), "flag me")
        resp = auth_client.post(
            f"/cms/workflow/api/case/{case.id}/findings/{f.id}/report-flag",
            json={"include_in_report": "nope"},
        )
        assert resp.status_code == 400

    def test_flag_is_case_scoped(self, auth_client, db_session):
        case = _orm_case()
        other = _orm_case("Other Case")
        f = _orm_finding(case, _admin_user(), "scoped")
        resp = auth_client.post(
            f"/cms/workflow/api/case/{other.id}/findings/{f.id}/report-flag",
            json={"include_in_report": False},
        )
        assert resp.status_code == 404


class TestReportRoutesRespectFlag:
    def test_case_report_html_excludes_flag_false(self, auth_client, db_session):
        case = _orm_case()
        _orm_finding(case, _admin_user(), "report-keep", include_in_report=None)
        drop = _orm_finding(case, _admin_user(), "report-drop", include_in_report=False)
        resp = auth_client.get(f"/cms/cases/{case.id}/report")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "report-keep" in text
        assert "report-drop" not in text
        assert db.session.get(Finding, drop.id).title == "report-drop"

    def test_workflow_pv_excludes_flag_false(self, auth_client, db_session):
        case = _orm_case()
        _orm_finding(case, _admin_user(), "pv-keep", include_in_report=True)
        _orm_finding(case, _admin_user(), "pv-drop", include_in_report=False)
        case.pv_body = "## Verslag\n<!-- pv-summary -->Summary<!-- /pv-summary -->"
        db.session.commit()

        resp = auth_client.get(f"/cms/workflow/case/{case.id}/pv")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "pv-keep" in text
        assert "pv-drop" not in text

    def test_template_report_excludes_flag_false(self, app, db_session):
        _admin_user()
        case = _orm_case()
        _orm_finding(case, _admin_user(), "tpl-keep")
        _orm_finding(case, _admin_user(), "tpl-drop", include_in_report=False)
        ctx = _build_report_context(case)
        titles = {f["title"] for f in ctx["findings"]}
        assert "tpl-keep" in titles
        assert "tpl-drop" not in titles
