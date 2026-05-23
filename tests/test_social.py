import pytest
import json
from datetime import datetime, timezone
from unittest.mock import patch


class TestSocialAccountCRUD:
    """Tests for add/delete social account routes."""

    def test_add_social_account_requires_auth(self, client):
        resp = client.post('/cms/api/subjects/1/social-accounts', json={
            'platform': 'GitHub',
            'username': 'testuser',
        })
        assert resp.status_code in (302, 401)

    def test_add_social_account(self, auth_client, app):
        from cms import db
        from cms.models import Subject, SocialAccount
        subject = Subject(name='Test Subject', subject_type='person')
        db.session.add(subject)
        db.session.commit()

        resp = auth_client.post(f'/cms/api/subjects/{subject.id}/social-accounts', json={
            'platform': 'GitHub',
            'username': 'testuser',
            'url': 'https://github.com/testuser',
            'account_id': '12345',
        })
        data = resp.get_json()
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {data}"
        assert data.get('account') is not None
        assert data['account']['platform'] == 'github'
        assert data['account']['username'] == 'testuser'

        account = SocialAccount.query.filter_by(subject_id=subject.id).first()
        assert account is not None
        assert account.platform == 'github'

    def test_add_social_account_missing_platform(self, auth_client, app):
        from cms import db
        from cms.models import Subject
        subject = Subject(name='Test Subject', subject_type='person')
        db.session.add(subject)
        db.session.commit()

        resp = auth_client.post(f'/cms/api/subjects/{subject.id}/social-accounts', json={
            'username': 'testuser',
        })
        assert resp.status_code == 400

    def test_add_social_account_missing_username(self, auth_client, app):
        from cms import db
        from cms.models import Subject
        subject = Subject(name='Test Subject', subject_type='person')
        db.session.add(subject)
        db.session.commit()

        resp = auth_client.post(f'/cms/api/subjects/{subject.id}/social-accounts', json={
            'platform': 'GitHub',
        })
        assert resp.status_code == 400

    def test_add_social_account_invalid_subject(self, auth_client):
        resp = auth_client.post('/cms/api/subjects/99999/social-accounts', json={
            'platform': 'GitHub',
            'username': 'testuser',
        })
        assert resp.status_code == 404

    def test_delete_social_account(self, auth_client, app):
        from cms import db
        from cms.models import Subject, SocialAccount
        subject = Subject(name='Test Subject', subject_type='person')
        db.session.add(subject)
        db.session.flush()
        account = SocialAccount(subject_id=subject.id, platform='github', username='testuser')
        db.session.add(account)
        db.session.commit()

        resp = auth_client.delete(f'/cms/api/subjects/{subject.id}/social-accounts/{account.id}')
        assert resp.status_code == 200

        deleted = db.session.get(SocialAccount, account.id)
        assert deleted is None

    def test_delete_social_account_not_found(self, auth_client):
        resp = auth_client.delete('/cms/api/subjects/1/social-accounts/99999')
        assert resp.status_code == 404

    def test_delete_social_account_wrong_subject(self, auth_client, app):
        from cms import db
        from cms.models import Subject, SocialAccount
        s1 = Subject(name='Subject 1', subject_type='person')
        s2 = Subject(name='Subject 2', subject_type='person')
        db.session.add_all([s1, s2])
        db.session.flush()
        account = SocialAccount(subject_id=s1.id, platform='github', username='testuser')
        db.session.add(account)
        db.session.commit()

        resp = auth_client.delete(f'/cms/api/subjects/{s2.id}/social-accounts/{account.id}')
        assert resp.status_code == 404


class TestCreateSubjectFromUsername:
    """Tests for creating a subject from a username."""

    def test_create_subject_from_username_requires_auth(self, client):
        resp = client.post('/cms/api/subjects/create-from-username', json={
            'username': 'testuser',
        })
        assert resp.status_code in (302, 401)

    def test_create_subject_from_username(self, auth_client, app):
        from cms import db
        from cms.models import Subject, SocialAccount
        resp = auth_client.post('/cms/api/subjects/create-from-username', json={
            'username': 'testuser',
            'platform': 'GitHub',
            'url': 'https://github.com/testuser',
        })
        data = resp.get_json()
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {data}"
        assert data.get('subject') is not None
        assert data.get('account') is not None
        assert 'testuser' in data['subject']['name']

        subject = db.session.get(Subject, data['subject']['id'])
        assert subject is not None
        assert subject.subject_type == 'person'

        account = SocialAccount.query.filter_by(subject_id=subject.id).first()
        assert account is not None
        assert account.platform == 'github'

    def test_create_subject_from_username_no_platform(self, auth_client, app):
        """Should default platform to 'other'."""
        resp = auth_client.post('/cms/api/subjects/create-from-username', json={
            'username': 'testuser',
        })
        data = resp.get_json()
        assert resp.status_code == 201
        assert data['subject']['name'] == 'testuser'

    def test_create_subject_from_username_from_url(self, auth_client, app):
        """Should extract username from URL when no username given."""
        with patch('cms.social_extractor.extract_username') as mock_extract:
            mock_extract.return_value = 'extracted_user'
            with patch('cms.social_extractor.detect_platform') as mock_detect:
                mock_detect.return_value = 'instagram'
                resp = auth_client.post('/cms/api/subjects/create-from-username', json={
                    'url': 'https://instagram.com/extracted_user',
                })
                data = resp.get_json()
                assert resp.status_code == 201
                assert 'extracted_user' in data['subject']['name']

    def test_create_subject_from_username_empty(self, auth_client):
        """Both username and url empty should return 400."""
        resp = auth_client.post('/cms/api/subjects/create-from-username', json={})
        assert resp.status_code == 400

    def test_create_subject_from_username_with_case(self, auth_client, app):
        from cms import db
        from cms.models import Client, Case, Subject
        client = Client(name='Test Client', contact_person='Test', contact_email='test@test.nl', is_active=True)
        db.session.add(client)
        db.session.flush()
        case = Case(case_number='C-001', client_id=client.id, title='Test Case', status='open', priority='medium', start_date=datetime.now(timezone.utc).date())
        db.session.add(case)
        db.session.commit()

        resp = auth_client.post('/cms/api/subjects/create-from-username', json={
            'username': 'testuser',
            'platform': 'GitHub',
            'case_id': str(case.id),
        })
        data = resp.get_json()
        assert resp.status_code == 201

        subject = db.session.get(Subject, data['subject']['id'])
        assert subject is not None
        assert case in subject.cases


class TestExtractSocialId:
    """Tests for social ID extraction route."""

    def test_extract_social_id_requires_auth(self, client):
        resp = client.post('/cms/extract-social-id', json={
            'url': 'https://instagram.com/testuser',
        })
        assert resp.status_code in (302, 401)

    def test_extract_social_id_no_url(self, auth_client):
        resp = auth_client.post('/cms/extract-social-id', json={})
        assert resp.status_code == 400

    def test_extract_social_id_with_extracted_data(self, auth_client, app):
        """Mock _extract_social_ids_from_url to return data."""
        from cms import db
        from cms.models import Subject
        subject = Subject(name='Test Subject', subject_type='person')
        db.session.add(subject)
        db.session.commit()

        with patch('cms.routes.social._extract_social_ids_from_url') as mock_extract:
            mock_extract.return_value = {
                'facebook_id': '123456789',
                'instagram_id': '987654321',
            }
            resp = auth_client.post('/cms/extract-social-id', json={
                'url': 'https://facebook.com/someuser',
                'subject_id': str(subject.id),
            })
            data = resp.get_json()
            assert resp.status_code == 200
            assert data['extracted']['facebook_id'] == '123456789'
            assert data['saved_to_subject'] is True

    def test_extract_social_id_no_extracted_fallback(self, auth_client, app):
        """When _extract_social_ids_from_url returns empty, should use social_extractor fallback."""
        with patch('cms.routes.social._extract_social_ids_from_url') as mock_extract:
            mock_extract.return_value = {}
            with patch('cms.social_extractor.detect_platform') as mock_detect:
                mock_detect.return_value = 'instagram'
                with patch('cms.social_extractor.extract_username') as mock_username:
                    mock_username.return_value = 'testuser'
                    resp = auth_client.post('/cms/extract-social-id', json={
                        'url': 'https://instagram.com/testuser',
                    })
                    data = resp.get_json()
                    assert resp.status_code == 200
                    assert data['extracted']['username'] == 'testuser'
                    assert data['platform'] == 'instagram'

    def test_extract_social_id_completely_empty(self, auth_client, app):
        """When everything returns empty, should return 200 with note."""
        with patch('cms.routes.social._extract_social_ids_from_url') as mock_extract:
            mock_extract.return_value = {}
            with patch('cms.social_extractor.detect_platform') as mock_detect:
                mock_detect.return_value = None
                with patch('cms.social_extractor.extract_username') as mock_username:
                    mock_username.return_value = None
                    resp = auth_client.post('/cms/extract-social-id', json={
                        'url': 'https://example.com',
                    })
                    data = resp.get_json()
                    assert resp.status_code == 200
                    assert 'note' in data
                    assert data['extracted'] == {}


class TestBulkExtractSocialIds:
    """Tests for bulk social ID extraction from findings."""

    def test_bulk_extract_requires_auth(self, client):
        resp = client.post('/cms/subjects/1/bulk-extract-social-ids')
        assert resp.status_code in (302, 401)

    def test_bulk_extract_no_findings(self, auth_client, app):
        from cms import db
        from cms.models import Subject
        subject = Subject(name='Test Subject', subject_type='person')
        db.session.add(subject)
        db.session.commit()

        resp = auth_client.post(f'/cms/subjects/{subject.id}/bulk-extract-social-ids')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['found'] == 0
        assert data['total'] == 0

    def test_bulk_extract_with_findings(self, auth_client, app):
        from cms import db
        from cms.models import Subject, Finding, SocialAccount, Client, Case
        client = Client(name='Test Client', contact_person='Test', contact_email='test@test.nl', is_active=True)
        db.session.add(client)
        db.session.flush()
        case = Case(case_number='C-001', client_id=client.id, title='Test Case', status='open', priority='medium', start_date=datetime.now(timezone.utc).date())
        db.session.add(case)
        db.session.flush()
        subject = Subject(name='Test Subject', subject_type='person')
        db.session.add(subject)
        db.session.flush()

        for i, url in enumerate([
            'https://instagram.com/testuser',
            'https://facebook.com/testuser',
            'https://twitter.com/testuser',
        ]):
            db.session.add(Finding(
                case_id=case.id, subject_id=subject.id,
                title=f'Finding {i}', content='Content',
                source_url=url, source_type='osint', finding_type='identity',
                created_by=1,
            ))
        db.session.commit()

        resp = auth_client.post(f'/cms/subjects/{subject.id}/bulk-extract-social-ids')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['found'] == 3
        assert data['total'] == 3

        accounts = SocialAccount.query.filter_by(subject_id=subject.id).all()
        assert len(accounts) == 3

    def test_bulk_extract_skips_duplicates(self, auth_client, app):
        from cms import db
        from cms.models import Subject, Finding, SocialAccount, Client, Case
        client = Client(name='Test Client', contact_person='Test', contact_email='test@test.nl', is_active=True)
        db.session.add(client)
        db.session.flush()
        case = Case(case_number='C-001', client_id=client.id, title='Test Case', status='open', priority='medium', start_date=datetime.now(timezone.utc).date())
        db.session.add(case)
        db.session.flush()
        subject = Subject(name='Test Subject', subject_type='person')
        db.session.add(subject)
        db.session.flush()

        db.session.add(Finding(
            case_id=case.id, subject_id=subject.id,
            title='Instagram find', content='Content',
            source_url='https://instagram.com/testuser',
            source_type='osint', finding_type='identity', created_by=1,
        ))
        db.session.add(SocialAccount(
            subject_id=subject.id, platform='instagram',
            username='testuser', url='https://instagram.com/testuser',
        ))
        db.session.commit()

        resp = auth_client.post(f'/cms/subjects/{subject.id}/bulk-extract-social-ids')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['found'] == 0
        assert data['skipped'] == 1
        assert data['total'] == 1
