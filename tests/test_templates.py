"""
Template smoke tests — verify every template compiles without Jinja2 syntax errors.
This catches missing {% endif %}, {% endfor %}, and other nesting bugs.
"""

import os


def test_all_templates_compile(app):
    """Every template in templates/cms/ compiles without Jinja2 syntax error."""
    errors = []
    compiled = 0

    for searchpath in app.jinja_loader.searchpath:
        if not os.path.isdir(searchpath):
            continue
        for root, _dirs, files in os.walk(searchpath):
            for f in files:
                if not f.endswith(".html"):
                    continue
                rel = os.path.relpath(os.path.join(root, f), searchpath)
                try:
                    app.jinja_env.get_template(rel)
                    compiled += 1
                except Exception as e:
                    errors.append(f"{rel}: {e}")

    assert errors == [], f"{len(errors)} template(s) failed to compile:\n" + "\n".join(
        errors
    )
    assert compiled > 0, "No templates found to compile"


def test_generate_report_post_without_template_rerenders_form(auth_client, app):
    """POST to /cms/cases/<id>/generate-report without template_id must re-render
    the existing generate_report.html form (200), not 500 on a missing template."""
    from datetime import datetime, timezone

    from cms import db
    from cms.models import Case, Client

    client = Client(
        name="Report Test",
        contact_person="T",
        contact_email="t@t.nl",
        is_active=True,
    )
    db.session.add(client)
    db.session.flush()
    case = Case(
        case_number="C-REPORT",
        client_id=client.id,
        title="Report Test",
        status="open",
        priority="medium",
        start_date=datetime.now(timezone.utc).date(),
    )
    db.session.add(case)
    db.session.commit()

    resp = auth_client.post(
        f"/cms/cases/{case.id}/generate-report",
        data={"template_id": "", "classification": "Confidential"},
    )

    assert resp.status_code == 200
    assert b"Choose a template" in resp.data
