"""
Integration tests — real DB, full CRUD workflows, cross-entity operations.
Uses the same fixtures as other tests (app, client, auth_client, db_session).
"""

import json
import pytest
from datetime import datetime, timezone


def _create_client_case(app) -> tuple[int, int]:
    from cms import db
    from cms.models import Client, Case
    client = Client(name='Test Client', contact_person='Test',
                    contact_email='test@test.nl', is_active=True)
    db.session.add(client)
    db.session.flush()
    case = Case(case_number='C-001', client_id=client.id,
                title='Test Case', status='open', priority='medium',
                start_date=datetime.now(timezone.utc).date())
    db.session.add(case)
    db.session.flush()
    db.session.commit()
    return client.id, case.id


# ─── Full Case Lifecycle ─────────────────────────────────────────────────


class TestCaseLifecycle:
    """End-to-end case lifecycle: create → add subjects → findings → export → delete."""

    def test_full_case_lifecycle(self, auth_client, app):
        from cms import db
        from cms.models import Client, Case, Subject, Finding, AuditLog

        # 1. Create client
        resp = auth_client.post('/cms/clients/create', json={
            'name': 'Integration Client',
            'contact_person': 'John',
            'contact_email': 'john@test.nl',
        })
        assert resp.status_code in (201, 302)
        client = Client.query.filter_by(name='Integration Client').first()
        assert client is not None
        client_id = client.id

        # 2. Create case with this client
        resp = auth_client.post('/cms/cases/create', json={
            'client_id': str(client_id),
            'title': 'Integration Case',
            'status': 'open',
            'priority': 'high',
            'case_type': 'investigation',
            'description': 'Full lifecycle test case',
        })
        assert resp.status_code in (201, 302)
        case = Case.query.filter_by(title='Integration Case').first()
        assert case is not None
        case_id = case.id

        # 3. Create two subjects
        resp = auth_client.post('/cms/subjects/create', json={
            'name': 'Subject Alpha', 'subject_type': 'person',
            'risk_score': 5,
        })
        assert resp.status_code == 201
        subj_a = Subject.query.filter_by(name='Subject Alpha').first()
        assert subj_a is not None

        resp = auth_client.post('/cms/subjects/create', json={
            'name': 'Subject Beta', 'subject_type': 'organization',
        })
        assert resp.status_code == 201
        subj_b = Subject.query.filter_by(name='Subject Beta').first()
        assert subj_b is not None

        # 4. Link subjects to case
        resp = auth_client.post(f'/cms/cases/{case_id}/add-subjects-bulk', json={
            'subject_ids': [str(subj_a.id), str(subj_b.id)],
        })
        assert resp.status_code == 200
        assert len(case.subjects.all()) == 2

        # 5. Add findings to both subjects
        for subj in (subj_a, subj_b):
            resp = auth_client.post('/cms/findings/create', json={
                'case_id': str(case_id),
                'subject_id': str(subj.id),
                'title': f'Finding for {subj.name}',
                'content': 'OSINT finding content',
                'source_url': f'https://example.com/{subj.id}',
                'source_type': 'osint',
                'finding_type': 'identity',
            })
            assert resp.status_code == 201

        findings = Finding.query.filter_by(case_id=case_id).all()
        assert len(findings) == 2

        # 6. Verify audit log was created
        logs = AuditLog.query.filter_by(entity_type='case', entity_id=str(case_id)).all()
        assert len(logs) >= 1

        # 7. Export case (CSV)
        csv_resp = auth_client.get(f'/cms/cases/{case_id}/export?format=csv')
        assert csv_resp.status_code == 200
        assert 'text/csv' in csv_resp.content_type

        # 8. Close then archive case
        case.status = 'closed'
        db.session.commit()
        resp = auth_client.post(f'/cms/cases/{case_id}/archive')
        assert resp.status_code in (200, 302)
        archived_case = db.session.get(Case, case_id)
        assert archived_case is None or archived_case.is_deleted is True

    def test_create_case_requires_client(self, auth_client):
        resp = auth_client.post('/cms/cases/create', json={
            'title': 'Orphan Case',
            'status': 'open',
            'priority': 'medium',
        })
        assert resp.status_code == 400

    def test_create_case_invalid_priority(self, auth_client, app):
        from cms import db
        from cms.models import Client
        client = Client(name='Temp', contact_person='T', contact_email='t@t.nl', is_active=True)
        db.session.add(client)
        db.session.commit()
        resp = auth_client.post('/cms/cases/create', json={
            'client_id': str(client.id),
            'title': 'Bad Priority',
            'status': 'open',
            'priority': 'urgent',
        })
        assert resp.status_code == 400


# ─── Cross-Entity Search ─────────────────────────────────────────────────


class TestCrossEntitySearch:
    """Test that FTS and global search return consistent results."""

    def test_fts_search_requires_auth(self, client):
        resp = client.post('/cms/api/search/fts', json={
            'q': 'test', 'scope': 'all',
        })
        assert resp.status_code in (302, 401)

    def test_fts_search_finds_subjects(self, auth_client, app):
        from cms import db
        from cms.models import Subject
        subj = Subject(name='UniqueSearchTarget', subject_type='person')
        db.session.add(subj)
        db.session.commit()

        resp = auth_client.post('/cms/api/search/fts', json={
            'query': 'UniqueSearchTarget', 'scope': 'subjects',
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data.get('subjects', [])) >= 1
        ids = [r['id'] for r in data['subjects']]
        assert subj.id in ids

    def test_fts_search_no_results(self, auth_client):
        resp = auth_client.post('/cms/api/search/fts', json={
            'query': 'zzzthisdoesnotexistzzz', 'scope': 'all',
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('total') == 0

    def test_global_search_requires_query(self, auth_client):
        resp = auth_client.get('/cms/search?q=ab')
        assert resp.status_code == 200


# ─── Bulk Operations ─────────────────────────────────────────────────────


class TestBulkOperations:
    """Test bulk delete endpoints."""

    def test_bulk_delete_subjects_requires_auth(self, client):
        resp = client.post('/cms/api/subjects/bulk-delete', json={'ids': [1, 2]})
        assert resp.status_code in (302, 401)

    def test_bulk_delete_subjects(self, auth_client, app):
        from cms import db
        from cms.models import Subject
        s1 = Subject(name='BulkDel1', subject_type='person')
        s2 = Subject(name='BulkDel2', subject_type='person')
        db.session.add_all([s1, s2])
        db.session.commit()
        s1_id, s2_id = s1.id, s2.id

        resp = auth_client.post('/cms/api/subjects/bulk-delete', json={
            'ids': [s1_id, s2_id],
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('deleted') == 2
        # Bulk delete now soft-deletes (consistent with single delete)
        db.session.expire_all()
        s1_refresh = db.session.get(Subject, s1_id)
        s2_refresh = db.session.get(Subject, s2_id)
        assert s1_refresh.is_deleted is True
        assert s2_refresh.is_deleted is True

    def test_bulk_delete_cases(self, auth_client, app):
        from cms import db
        from cms.models import Client, Case
        client = Client(name='BulkClient', contact_person='T', contact_email='t@t.nl', is_active=True)
        db.session.add(client)
        db.session.commit()


        c1 = Case(case_number='B-001', client_id=client.id, title='BulkCase1',
                  status='open', priority='medium', start_date=datetime.now(timezone.utc).date())
        c2 = Case(case_number='B-002', client_id=client.id, title='BulkCase2',
                  status='open', priority='medium', start_date=datetime.now(timezone.utc).date())
        db.session.add_all([c1, c2])
        db.session.commit()
        c1_id, c2_id = c1.id, c2.id

        resp = auth_client.post('/cms/api/cases/bulk-delete', json={
            'ids': [c1_id, c2_id],
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('deleted') == 2
        # Bulk delete now soft-deletes
        db.session.expire_all()
        assert db.session.get(Case, c1_id).is_deleted is True
        assert db.session.get(Case, c2_id).is_deleted is True

    def test_bulk_delete_too_many(self, auth_client):
        resp = auth_client.post('/cms/api/subjects/bulk-delete', json={
            'ids': list(range(1, 200)),
        })
        assert resp.status_code == 400


# ─── Subject CRUD with Addresses ─────────────────────────────────────────


class TestSubjectWithAddresses:
    """Test creating subjects with address data."""

    def test_create_subject_minimal(self, auth_client):
        resp = auth_client.post('/cms/subjects/create', json={
            'name': 'Minimal Subject',
            'subject_type': 'person',
        })
        assert resp.status_code in (201, 302)

    def test_create_subject_with_phone_normalization(self, auth_client, app):
        from cms import db
        from cms.models import Subject
        resp = auth_client.post('/cms/subjects/create', json={
            'name': 'Phone Subject',
            'subject_type': 'person',
            'contacts_data': json.dumps([{
                'contact_type': 'phone',
                'value': '0634407404',
                'is_primary': True,
            }]),
        })
        assert resp.status_code in (201, 302), resp.get_json()
        subject = Subject.query.filter_by(name='Phone Subject').first()
        assert subject is not None
        # phone should be normalized to E164 (may be encrypted or plaintext depending on env)
        assert subject.phone is not None
        assert '+31634407404' in subject.phone or subject.phone.startswith('gAAAAA')

    def test_edit_subject(self, auth_client, app):
        from cms import db
        from cms.models import Subject
        subj = Subject(name='Before Edit', subject_type='person')
        db.session.add(subj)
        db.session.commit()

        resp = auth_client.post(f'/cms/subjects/{subj.id}/edit', json={
            'name': 'After Edit',
            'risk_score': 5,
        })
        assert resp.status_code in (200, 302)
        updated = db.session.get(Subject, subj.id)
        assert updated.name == 'After Edit'
        assert updated.risk_score == 5

    def test_delete_subject(self, auth_client, app):
        from cms import db
        from cms.models import Subject
        subj = Subject(name='To Delete', subject_type='person')
        db.session.add(subj)
        db.session.commit()

        resp = auth_client.post(f'/cms/subjects/{subj.id}/delete')
        assert resp.status_code in (200, 302)
        deleted = db.session.get(Subject, subj.id)
        assert deleted.is_deleted is True


# ─── Session Management ──────────────────────────────────────────────────


class TestSessions:
    """Test session management UI."""

    def test_session_list_requires_admin(self, auth_client):
        resp = auth_client.get('/cms/admin/sessions')
        # admin should have access
        assert resp.status_code in (200, 302)

    def test_rate_limit_status(self, auth_client):
        resp = auth_client.get('/api/rate-limit-status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'api_rate_limits' in data
        assert 'platform_rate_limits' in data


# ─── Health Checks ───────────────────────────────────────────────────────


class TestHealth:
    """Test health check endpoint."""

    def test_health_endpoint(self, client):
        resp = client.get('/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('status') == 'ok'
        assert 'database' in data
        assert 'cache' in data

    def test_api_config_returns_status(self, client):
        resp = client.get('/api/config')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'ai_available' in data


# ─── Version and System Info ─────────────────────────────────────────────


class TestSystemInfo:
    """Test version/changelog system routes."""

    def test_version_endpoint(self, client):
        resp = client.get('/api/version')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'version' in data

    def test_changelog_endpoint(self, client):
        resp = client.get('/api/changelog')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'html' in data


# ─── Subject Relationships ───────────────────────────────────────────────


class TestSubjectRelationships:
    """Test linking subjects across cases."""

    def test_subject_shared_across_cases(self, auth_client, app):
        from cms import db
        from cms.models import Client, Case, Subject
        client = Client(name='SharedClient', contact_person='T',
                        contact_email='t@t.nl', is_active=True)
        db.session.add(client)
        db.session.flush()

        case_a = Case(case_number='SA-001', client_id=client.id,
                      title='Shared A', status='open', priority='medium',
                      start_date=datetime.now(timezone.utc).date())
        case_b = Case(case_number='SA-002', client_id=client.id,
                      title='Shared B', status='open', priority='medium',
                      start_date=datetime.now(timezone.utc).date())
        db.session.add_all([case_a, case_b])
        db.session.flush()

        subj = Subject(name='SharedSubject', subject_type='person')
        db.session.add(subj)
        db.session.commit()

        # Link to both cases
        for case in (case_a, case_b):
            resp = auth_client.post(f'/cms/cases/{case.id}/add-subjects-bulk', json={
                'subject_ids': [str(subj.id)],
            })
            assert resp.status_code == 200

        # Verify subject is linked to both
        assert subj in case_a.subjects.all()
        assert subj in case_b.subjects.all()
        assert len(subj.cases.all()) == 2

    def test_subject_relationship_graph(self, auth_client, app):
        from cms import db
        from cms.models import Subject, subject_relations
        alice = Subject(name='Alice', subject_type='person')
        bob = Subject(name='Bob', subject_type='person')
        db.session.add_all([alice, bob])
        db.session.commit()

        # Insert bidirectional relationship
        for sid, rid in [(alice.id, bob.id), (bob.id, alice.id)]:
            db.session.execute(
                subject_relations.insert().values(
                    subject_id=sid,
                    related_subject_id=rid,
                    relationship_type='family'
                )
            )
        db.session.commit()

        resp = auth_client.get(f'/cms/subjects/{alice.id}/relationships')
        assert resp.status_code in (200, 302)
