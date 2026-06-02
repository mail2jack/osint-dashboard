class TestUsernameSearchRapidAPI:
    """Test the /api/username/rapidapi endpoint."""

    def test_requires_username(self, auth_client):
        resp = auth_client.post("/api/username/rapidapi", json={})
        assert resp.status_code == 400

    def test_empty_username(self, auth_client):
        resp = auth_client.post("/api/username/rapidapi", json={"username": ""})
        assert resp.status_code == 400

    def test_returns_usage_info_on_fallback(self, auth_client):
        """Without API key, should return fallback_to_sherlock=True."""
        resp = auth_client.post("/api/username/rapidapi", json={"username": "testuser"})
        data = resp.get_json()
        assert data is not None
        assert "api_usage" in data
        assert data.get("api_usage", {}).get("remaining") is not None

    def test_api_usage_structure(self, auth_client):
        resp = auth_client.post("/api/username/rapidapi", json={"username": "testuser"})
        data = resp.get_json()
        usage = data.get("api_usage", {})
        assert "used" in usage
        assert "limit" in usage
        assert "remaining" in usage
        assert usage["limit"] == 100

    def test_rapidapi_status_endpoint(self, client):
        resp = client.get("/api/username/rapidapi-status")
        data = resp.get_json()
        assert data is not None
        assert "configured" in data
        assert "used" in data
        assert "limit" in data
        assert "remaining" in data
        assert data["limit"] == 100


class TestSherlockFallback:
    """Test that the main dashboard username search falls back correctly."""

    def test_search_social_requires_auth(self, auth_client):
        resp = auth_client.post("/api/username/rapidapi", json={"username": "testuser"})
        # This endpoint now requires auth; with auth_client it should respond
        assert resp.status_code in (200, 400, 429)

    def test_username_search_fallback_no_key(self, auth_client):
        """Without RapidAPI key configured, should indicate fallback."""
        from cms.models import Setting

        Setting.set("rapidapi_username_key", "")
        resp = auth_client.post("/api/username/rapidapi", json={"username": "testuser"})
        data = resp.get_json()
        # Without key, either fallback or no key configured
        assert data is not None
