"""
PR1 — Terminologie, routes en navigatie (ADR-0002 naamgevingssweep).

Verifieert dat case-level UI voortaan "Zaak/Zaken" (NL) / "Case/Cases" (EN)
heet via i18n, dat "Onderzoek/Onderzoeken" alleen voor het child-object
wordt gebruikt, dat de navigatie is samengevoegd (Entity-dropdown met
workflow-ingang + "Legacy-zaken"), en dat de nav-dropdown klick/touch- en
toetsenbordtoegankelijk is (aria-expanded, Escape).

Deze tests zijn bedoeld voor de draft-PR review-baseline; ze dekken de
hoofdnavigatie, dashboard, case-create, case-detail, het
child-investigations-overzicht, het PV en het Subject Profile — in NL en EN.
"""

from cms.models import Case, FeatureFlag, User, db


def _set_lang(client, lang):
    with client.session_transaction() as sess:
        sess["lang"] = lang
    from flask import g

    g.pop("_flask_babel", None)
    return client


def _admin():
    return User.query.filter_by(username="admin").first()


def _enable_flag(tenant_id):
    flag = FeatureFlag(
        tenant_id=tenant_id,
        flag_name="subject_first_investigations",
        enabled=True,
    )
    db.session.add(flag)
    db.session.commit()
    return flag


def _case_with_subject(auth_client, title="Nav Terminology Case"):
    resp = auth_client.post(
        "/cms/workflow/case/new",
        data={
            "client_name": "Nav Test Client",
            "title": title,
            "subject_0_name": "Nav Test Person",
            "subject_0_type": "person",
            "subject_0_email": "nav@example.com",
            "priority": "medium",
        },
    )
    assert resp.status_code in (200, 302)
    case = Case.query.filter_by(title=title).first()
    assert case is not None
    return case


class TestMainNav:
    def test_nl_dashboard_no_case_level_investigations(self, auth_client):
        _set_lang(auth_client, "nl")
        resp = auth_client.get("/cms/workflow/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        # Samengevoegde navigatie: Entity-dropdown-item "Zaken" -> workflow.dashboard
        assert "Zaken" in html
        assert "Legacy-zaken" in html
        assert "/cms/workflow/" in html
        # "Onderzoeken" is gereserveerd voor het child-object; hoort niet op het
        # case-level dashboard te verschijnen (geen aparte top-level-nav-link meer).
        assert "Onderzoeken" not in html

    def test_en_dashboard_labels(self, auth_client):
        _set_lang(auth_client, "en")
        resp = auth_client.get("/cms/workflow/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert "Cases" in html
        assert "Legacy cases" in html
        assert "Onderzoeken" not in html

    def test_empty_state_contains_new_case_cta(self, auth_client):
        _set_lang(auth_client, "nl")
        resp = auth_client.get("/cms/workflow/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Nieuwe zaak" in html

    def test_nav_dropdown_semantics_and_keyboard(self, auth_client):
        _set_lang(auth_client, "nl")
        resp = auth_client.get("/cms/workflow/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        # Trigger is een echte <button> met ARIA-controller, geen lege <a href="#">.
        assert 'class="nav-dropdown-trigger" aria-haspopup="true" aria-expanded="false" aria-controls="entity-nav-menu">' in html
        assert 'id="entity-nav-menu"' in html
        # Toetsenbord: ArrowDown opent en verplaatst focus; Escape sluit.
        assert "e.key === 'ArrowDown'" in html
        assert "Escape" in html
        # Open-state wordt ALLEEN door aria-expanded/is-open gestuurd (geen CSS-hover).
        assert ".nav-dropdown:hover .nav-dropdown-menu" not in html

    def test_nav_dropdown_no_fake_menu_roles(self, auth_client):
        _set_lang(auth_client, "nl")
        resp = auth_client.get("/cms/workflow/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        # Entity-dropdown is een gewone nav-disclosure: geen rol=menu/menuitem.
        assert 'role="menu"' not in html
        assert 'role="menuitem"' not in html
        # Trigger is geen link-anker met dead href="#".
        assert '<a href="#" class="nav-dropdown-trigger"' not in html


class TestCaseCreate:
    def test_nl_case_create_labels(self, auth_client):
        _set_lang(auth_client, "nl")
        resp = auth_client.get("/cms/workflow/case/new")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert "Workflow — Nieuwe zaak" in html
        assert ">Nieuwe zaak →" in html
        assert "Onderzoeken" not in html

    def test_en_case_create_labels(self, auth_client):
        _set_lang(auth_client, "en")
        resp = auth_client.get("/cms/workflow/case/new")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert "Workflow — New case" in html
        assert ">New case →" in html


class TestCaseDetail:
    def test_nl_detail_breadcrumb_steps_child_label(self, auth_client):
        _set_lang(auth_client, "nl")
        case = _case_with_subject(auth_client, title="Detail NL")
        resp = auth_client.get(f"/cms/workflow/case/{case.id}")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert "Zaken" in html  # breadcrumb naar werkvoorraad
        assert "Onderzoeksacties" in html  # Step 3 (research actions)
        assert "Onderzoeken" in html  # Step 4: child-object-label

    def test_en_detail_breadcrumb_steps_child_label(self, auth_client):
        _set_lang(auth_client, "en")
        case = _case_with_subject(auth_client, title="Detail EN")
        resp = auth_client.get(f"/cms/workflow/case/{case.id}")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert "Cases" in html
        assert "Research actions" in html
        assert "Investigations" in html  # child-object-label (EN)


class TestChildInvestigationsOverview:
    def test_nl_overview_breadcrumb_and_section(self, auth_client):
        _set_lang(auth_client, "nl")
        case = _case_with_subject(auth_client, title="Overview NL")
        resp = auth_client.get(f"/cms/workflow/case/{case.id}/investigations")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert "Zaken" in html  # breadcrumb (case-level)
        assert "Onderzoeken" in html  # pagina toont de child-Onderzoeken

    def test_en_overview_breadcrumb_and_section(self, auth_client):
        _set_lang(auth_client, "en")
        case = _case_with_subject(auth_client, title="Overview EN")
        resp = auth_client.get(f"/cms/workflow/case/{case.id}/investigations")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert "Cases" in html
        assert "Investigations" in html


class TestPVReport:
    def test_nl_pv_title(self, auth_client):
        _set_lang(auth_client, "nl")
        case = _case_with_subject(auth_client, title="PV NL")
        resp = auth_client.get(f"/cms/workflow/case/{case.id}/pv")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "<h1>Zaakverslag</h1>" in html

    def test_en_pv_title(self, auth_client):
        _set_lang(auth_client, "en")
        case = _case_with_subject(auth_client, title="PV EN")
        resp = auth_client.get(f"/cms/workflow/case/{case.id}/pv")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "<h1>Case report</h1>" in html


class TestCaseExportNl:
    """P1-regressie: exportverslag gebruikt de bestaande i18n-key 'Case Report'
    (niet de nieuwe 'Case report'-key van de pv-titel), zodat de NL-export-tekst
    behouden blijft."""

    def test_nl_export_csv_uses_case_report_key(self, auth_client):
        _set_lang(auth_client, "nl")
        case = _case_with_subject(auth_client, title="Export NL")
        resp = auth_client.get(f"/cms/cases/{case.id}/export?format=csv")
        assert resp.status_code == 200
        csv_data = resp.get_data(as_text=True)

        # Eerste rij = gettext("Case Report") -> NL "Dossier rapport".
        assert "Dossier rapport" in csv_data
        assert "Case Report" not in csv_data


class TestSubjectProfileResearchActions:
    def test_nl_profile_tab_labels(self, auth_client):
        _set_lang(auth_client, "nl")
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Profile NL")
        subject = case.subjects[0]

        resp = auth_client.get(f"/cms/subjects/{subject.id}/profile")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert "Onderzoeksacties" in html  # tab-label
        assert "Actie voorstellen" in html  # propose-card
        # Autocomplete-group-label: i18n via data-attribuut (geen hardcoded JS-string).
        assert 'data-autocomplete="relation-subject"' in html
        assert 'data-i18n-in-case="In deze zaak"' in html
        assert "'In this case'" not in html  # geen hardcoded EN-string in de JS

    def test_en_profile_tab_labels(self, auth_client):
        _set_lang(auth_client, "en")
        admin = _admin()
        _enable_flag(admin.tenant_id)
        case = _case_with_subject(auth_client, title="Profile EN")
        subject = case.subjects[0]

        resp = auth_client.get(f"/cms/subjects/{subject.id}/profile")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert "Research actions" in html
        assert "Propose action" in html
        # Autocomplete-group-label in EN: data-attribuut vertaald als originaal msgid.
        assert 'data-i18n-in-case="In this case"' in html
        # De JS leest het attribuut (getAttribute), niet een hardcoded literal.
        assert "getAttribute('data-i18n-in-case')" in html