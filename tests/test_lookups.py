import pytest
from unittest.mock import patch, MagicMock


class MockHttpxResponse:
    def __init__(self, status_code=200, json_data=None, text=''):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f'HTTP {self.status_code}')


class MockRequestsResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


# ── Email Check ──────────────────────────────────────────────────────────

class TestEmailCheck:
    URL = '/cms/api/email-check'

    def test_requires_auth(self, client):
        resp = client.post(self.URL, json={'email': 'test@example.com'})
        assert resp.status_code == 401

    def test_missing_email(self, auth_client):
        resp = auth_client.post(self.URL, json={})
        assert resp.status_code == 400

    def test_invalid_format(self, auth_client):
        resp = auth_client.post(self.URL, json={'email': 'not-an-email'})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get('valid_format') is False

    def test_valid_email(self, auth_client):
        resp = auth_client.post(self.URL, json={'email': 'test@example.com'})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get('valid_format') is True
        assert data.get('domain') == 'example.com'
        assert 'search_links' in data


# ── Kadaster Lookup ──────────────────────────────────────────────────────

class TestKadasterLookup:
    URL = '/cms/api/kadaster-lookup'

    def test_requires_auth(self, client):
        resp = client.post(self.URL, json={'zipcode': '1234AB', 'number': '1'})
        assert resp.status_code == 401

    def test_missing_query(self, auth_client):
        resp = auth_client.post(self.URL, json={})
        assert resp.status_code == 400

    @patch('httpx.get')
    def test_happy_path(self, mock_get, auth_client):
        mock_get.return_value = MockHttpxResponse(
            status_code=200,
            json_data={
                'response': {
                    'docs': [{
                        'straatnaam': 'Hoofdstraat',
                        'huisnummer': '1',
                        'postcode': '1234AB',
                        'woonplaatsnaam': 'Amsterdam',
                        'gemeentenaam': 'Amsterdam',
                        'provincienaam': 'Noord-Holland',
                        'centroide_ll': 'POINT(4.9 52.37)',
                        'gebruiksdoel': 'woning',
                        'oppervlakte': '120',
                        'bouwjaar': '1990',
                        'bag_id': '123456',
                        'status': 'in gebruik',
                        'type': 'verblijfsobject',
                        'huisletter': '',
                        'huisnummertoevoeging': '',
                    }]
                }
            }
        )
        resp = auth_client.post(self.URL, json={'zipcode': '1234AB', 'number': '1'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('found') is True
        assert data['bag_data']['street'] == 'Hoofdstraat'
        assert data['bag_data']['town'] == 'Amsterdam'

    @patch('httpx.get')
    def test_not_found(self, mock_get, auth_client):
        mock_get.return_value = MockHttpxResponse(
            status_code=200,
            json_data={'response': {'docs': []}}
        )
        resp = auth_client.post(self.URL, json={'zipcode': '9999ZZ', 'number': '999'})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get('found') is False


# ── Politiebureau Lookup ─────────────────────────────────────────────────

class TestPolitiebureauLookup:
    URL = '/cms/api/politiebureau-lookup'

    def test_requires_auth(self, client):
        resp = client.post(self.URL, json={'lat': 52.37, 'lon': 4.9})
        assert resp.status_code == 401

    @patch('httpx.get')
    def test_happy_path(self, mock_get, auth_client):
        mock_get.return_value = MockHttpxResponse(
            status_code=200,
            json_data={
                'politiebureaus': [{
                    'naam': 'Politiebureau Amsterdam Centrum',
                    'bezoekadres': {
                        'adres': 'Nieuwezijds Voorburgwal 100',
                        'postcode': '1000AA',
                        'plaats': 'Amsterdam',
                    },
                    'telefoonnummer': '0900-8844',
                    'openingstijden': '24/7',
                    'url': 'https://www.politie.nl/amsterdam',
                    'locaties': [{}],
                }]
            }
        )
        resp = auth_client.post(self.URL, json={'lat': 52.37, 'lon': 4.9})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('found') is True
        assert data['station']['name'] == 'Politiebureau Amsterdam Centrum'

    def test_no_coordinates(self, auth_client):
        resp = auth_client.post(self.URL, json={})
        assert resp.status_code == 400


# ── RDW Vehicle Check ───────────────────────────────────────────────────

class TestRDWCheck:
    URL = '/cms/check-rdw-vehicle'

    def test_requires_auth(self, client):
        resp = client.post(self.URL, json={'kenteken': '22PBR2'})
        assert resp.status_code == 401

    def test_missing_kenteken(self, auth_client):
        resp = auth_client.post(self.URL, json={})
        assert resp.status_code == 400

    @patch('cms.routes.lookups.http_requests.get')
    def test_happy_path(self, mock_get, auth_client):
        mock_get.return_value = MockRequestsResponse(
            status_code=200,
            json_data=[{
                'kenteken': '22PBR2',
                'merk': 'VOLKSWAGEN',
                'handelsbenaming': 'GOLF',
                'voertuigsoort': 'Personenauto',
                'eerste_kleur': 'Zwart',
                'inrichting': 'hatchback',
                'aantal_deuren': '5',
                'aantal_zitplaatsen': '5',
                'datum_eerste_toelating': '20200101',
                'catalogusprijs': '35000',
            }]
        )
        resp = auth_client.post(self.URL, json={'kenteken': '22-PBR-2'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('found') is True
        assert data.get('merk') == 'VOLKSWAGEN'
        assert data.get('kenteken_display') == '22-PBR-2'

    @patch('cms.routes.lookups.http_requests.get')
    def test_not_found(self, mock_get, auth_client):
        mock_get.return_value = MockRequestsResponse(status_code=200, json_data=[])
        resp = auth_client.post(self.URL, json={'kenteken': 'XX-99-YY'})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data.get('found') is False

    @patch('cms.routes.lookups.http_requests.get')
    def test_api_error(self, mock_get, auth_client):
        mock_get.return_value = MockRequestsResponse(status_code=503)
        resp = auth_client.post(self.URL, json={'kenteken': '22PBR2'})
        assert resp.status_code == 502


# ── RDW Update Subject ──────────────────────────────────────────────────

class TestRDWUpdateSubject:
    URL = '/cms/subjects/'

    def test_requires_auth(self, client, db_session):
        resp = client.post(f'{self.URL}999/update-from-rdw', json={'kenteken': '22PBR2'})
        assert resp.status_code == 401

    def test_subject_not_found(self, auth_client):
        resp = auth_client.post(f'{self.URL}999/update-from-rdw', json={'kenteken': '22PBR2'})
        assert resp.status_code == 404

    def test_subject_not_vehicle(self, auth_client, db_session):
        from cms.models import Subject
        subj = Subject(name='Not a car', subject_type='person')
        db_session.add(subj)
        db_session.flush()
        sid = subj.id
        resp = auth_client.post(f'{self.URL}{sid}/update-from-rdw', json={'kenteken': '22PBR2'})
        assert resp.status_code == 400
        assert 'not a vehicle' in resp.get_json().get('error', '').lower()


# ── Vessel Lookup ───────────────────────────────────────────────────────

class TestVesselLookup:
    URL = '/cms/api/vessel-lookup'

    def test_requires_auth(self, client):
        resp = client.post(self.URL, json={'name': 'Titanic'})
        assert resp.status_code == 401

    def test_no_params(self, auth_client):
        resp = auth_client.post(self.URL, json={})
        assert resp.status_code == 400

    @patch('cms.routes.lookups.lookup_vessel')
    def test_happy_path(self, mock_lookup, auth_client):
        mock_lookup.return_value = {
            'found': True,
            'name': 'EVER GIVEN',
            'imo': '9811000',
            'mmsi': '353136000',
            'flag': 'Panama',
        }
        resp = auth_client.post(self.URL, json={'imo': '9811000'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('found') is True
        assert data['name'] == 'EVER GIVEN'

    @patch('cms.routes.lookups.lookup_vessel')
    def test_not_found(self, mock_lookup, auth_client):
        mock_lookup.return_value = {'found': False, 'message': 'No vessel data found'}
        resp = auth_client.post(self.URL, json={'name': 'NONEXISTENT'})
        data = resp.get_json()
        assert data.get('found') is False


# ── Interpol / Politie Check ─────────────────────────────────────────────

class TestCheckPolicieData:
    URL = '/cms/check-policie-data'

    def test_requires_auth(self, client):
        resp = client.post(self.URL, json={'name': 'John Doe'})
        assert resp.status_code == 401

    @patch('cms.routes.lookups._check_interpol_rate_limit')
    def test_no_name(self, mock_rate, auth_client):
        mock_rate.return_value = 0
        resp = auth_client.post(self.URL, json={})
        assert resp.status_code == 400


class TestCheckPolicieStatus:
    URL = '/cms/check-policie-data-status'

    def test_requires_auth(self, client):
        resp = client.get(self.URL)
        # GET with login_required redirects instead of 401
        assert resp.status_code in (302, 401)

    @patch('cms.routes.lookups._check_interpol_rate_limit')
    def test_status_endpoint(self, mock_rate, auth_client):
        mock_rate.return_value = 0
        with patch('httpx.get') as mock_get:
            mock_get.return_value = MockHttpxResponse(status_code=200, json_data={})
            resp = auth_client.get(self.URL)
            data = resp.get_json()
            assert resp.status_code == 200
            assert 'available' in data
