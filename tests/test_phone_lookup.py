import pytest
import json
from unittest.mock import patch, MagicMock


class MockHttpxResponse:
    """Helper to create mock httpx responses."""
    def __init__(self, status_code=200, json_data=None, text=''):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f'HTTP {self.status_code}')


class TestPhoneLookup:
    """Test the /cms/api/phone-lookup endpoint."""

    def test_requires_auth(self, client):
        """API endpoints return 401 for unauthenticated JSON requests."""
        resp = client.post('/cms/api/phone-lookup', json={'phone': '+31612345678'})
        assert resp.status_code == 401

    def test_missing_phone(self, auth_client):
        resp = auth_client.post('/cms/api/phone-lookup', json={})
        data = resp.get_json()
        assert resp.status_code == 400
        assert 'error' in data

    @patch('httpx.Client')
    @patch('httpx.get')
    def test_valid_nl_number(self, mock_get, mock_client, auth_client):
        """Valid NL number returns validation data (no external calls needed for validation)."""
        mock_get.return_value = MockHttpxResponse(status_code=503)
        mock_client.return_value.__enter__.return_value.get.return_value = MockHttpxResponse(
            status_code=200, text='phone number is not on whatsapp'
        )

        resp = auth_client.post('/cms/api/phone-lookup', json={'phone': '+31634407404'})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get('valid') is True
        assert data.get('country') is not None
        assert data.get('formatted') == '+31634407404'
        assert data.get('line_type') is not None

    @patch('httpx.Client')
    @patch('httpx.get')
    def test_international_number(self, mock_get, mock_client, auth_client):
        mock_get.return_value = MockHttpxResponse(status_code=503)
        mock_client.return_value.__enter__.return_value.get.return_value = MockHttpxResponse(
            status_code=200, text='phone number is not on whatsapp'
        )

        resp = auth_client.post('/cms/api/phone-lookup', json={'phone': '+14155552671'})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get('valid') is True
        assert data.get('country_code') == '+1'
        assert data.get('formatted') == '+14155552671'

    @patch('httpx.Client')
    @patch('httpx.get')
    def test_invalid_number(self, mock_get, mock_client, auth_client):
        mock_get.return_value = MockHttpxResponse(status_code=503)
        mock_client.return_value.__enter__.return_value.get.return_value = MockHttpxResponse(
            status_code=200, text='phone number is not on whatsapp'
        )

        resp = auth_client.post('/cms/api/phone-lookup', json={'phone': '123'})
        data = resp.get_json()
        assert data.get('valid') is False

    @patch('httpx.Client')
    @patch('httpx.get')
    def test_normalizes_phone(self, mock_get, mock_client, auth_client):
        """Test that Dutch numbers with various formats are normalized to E164."""
        mock_get.return_value = MockHttpxResponse(status_code=503)
        mock_client.return_value.__enter__.return_value.get.return_value = MockHttpxResponse(
            status_code=200, text='phone number is not on whatsapp'
        )

        resp = auth_client.post('/cms/api/phone-lookup', json={'phone': '06 12345678'})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get('valid') is True
        assert data.get('formatted', '').startswith('+31')

    @patch('httpx.Client')
    @patch('httpx.get')
    def test_normalize_leading_zero(self, mock_get, mock_client, auth_client):
        mock_get.return_value = MockHttpxResponse(status_code=503)
        mock_client.return_value.__enter__.return_value.get.return_value = MockHttpxResponse(
            status_code=200, text='phone number is not on whatsapp'
        )

        resp = auth_client.post('/cms/api/phone-lookup', json={'phone': '0612345678'})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get('valid') is True
        assert 'Netherlands' in str(data.get('country')) or 'NL' in str(data.get('country'))
