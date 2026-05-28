"""
Route smoke tests — hit every GET endpoint with an authenticated client.
Catches 500 errors from KeyErrors, missing template variables, etc.
"""

import pytest


class TestRoutesSmoke:

    def test_static_get_routes(self, auth_client):
        """
        Hit every GET route that has no URL path parameters.
        These are the dashboard, list pages, help pages, etc.
        """
        errors = []
        tested = 0

        for rule in auth_client.application.url_map.iter_rules():
            # Only GET routes, skip routes with path params like <id>
            if 'GET' not in rule.methods:
                continue
            if '<' in rule.rule:
                continue
            # Skip auth routes (tested elsewhere) and API endpoints
            if rule.rule.startswith('/auth/'):
                continue
            if rule.rule.startswith('/static/'):
                continue
            if rule.rule.startswith('/health'):
                continue

            try:
                resp = auth_client.get(rule.rule)
                if resp.status_code == 500:
                    errors.append(f'GET {rule.rule} → 500')
                else:
                    tested += 1
            except Exception as e:
                errors.append(f'GET {rule.rule} → {e}')

        assert not errors, \
            f'{len(errors)} route(s) returned 500:\n' + '\n'.join(errors)
        assert tested > 0, 'No routes were tested'

    def test_detail_pages_with_real_data(self, auth_client, app):
        """
        Hit detail pages for cases, subjects, clients, etc.
        with real data seeded into the DB.
        """
        from cms import db
        from cms.models import Client, Case, Subject, Finding, User
        from datetime import datetime, timezone

        admin = User.query.filter_by(username='admin').first()
        client = Client(name='Detail Test', contact_person='T',
                        contact_email='t@t.nl', is_active=True)
        db.session.add(client)
        db.session.flush()
        case = Case(case_number='C-DETAIL', client_id=client.id,
                    title='Detail Test', status='open', priority='medium',
                    start_date=datetime.now(timezone.utc).date())
        db.session.add(case)
        db.session.flush()
        subject = Subject(name='Detail Subject', subject_type='person')
        db.session.add(subject)
        db.session.flush()
        finding = Finding(case_id=case.id, subject_id=subject.id,
                          title='Detail Finding', content='test',
                          source_type='manual', created_by=admin.id)
        db.session.add(finding)
        case.subjects.append(subject)
        db.session.commit()

        detail_routes = [
            f'/cms/cases/{case.id}',
            f'/cms/cases/{case.id}/edit',
            f'/cms/cases/{case.id}/report',
            f'/cms/clients/{client.id}',
            f'/cms/clients/{client.id}/edit',
            f'/cms/subjects/{subject.id}',
            f'/cms/subjects/{subject.id}/edit',
            f'/cms/help/cases',
            f'/cms/api/help/cases',
        ]

        errors = []
        for url in detail_routes:
            try:
                resp = auth_client.get(url)
                if resp.status_code == 500:
                    errors.append(f'GET {url} → 500')
            except Exception as e:
                errors.append(f'GET {url} → {e}')

        # Cleanup
        db.session.rollback()

        assert not errors, \
            f'{len(errors)} detail route(s) returned 500:\n' + '\n'.join(errors)
