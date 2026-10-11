"""Regression coverage for the findings dashboard overview and drill-downs."""


def test_findings_dashboard_has_drilldown_cards(auth_client):
    response = auth_client.get("/cms/workflow/findings/dashboard")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'href="/cms/workflow/findings?status=candidate"' in html
    assert 'href="/cms/workflow/findings?status=verified"' in html
    assert 'href="/cms/workflow/findings?report=in"' in html
