class TestExtractSocialId:
    URL = "/cms/extract-social-id"

    def test_extract_requires_auth(self, client):
        resp = client.post(self.URL, json={"url": "https://example.com"})
        assert resp.status_code in (302, 401)

    def test_check_requires_auth(self, client):
        resp = client.get("/cms/subjects/x/social-ids")
        assert resp.status_code in (302, 401)

    def test_extract_missing_url(self, auth_client):
        resp = auth_client.post(self.URL, json={})
        assert resp.status_code == 400

    def test_extract_invalid_url(self, auth_client):
        resp = auth_client.post(self.URL, json={"url": ""})
        assert resp.status_code == 400

    def test_extract_nonsocial_url(self, auth_client, app):
        from cms import db
        from cms.models import Subject

        subject = Subject(name="Social Test", subject_type="person")
        db.session.add(subject)
        db.session.commit()
        resp = auth_client.post(
            self.URL,
            json={
                "url": "https://example.com",
                "subject_id": subject.id,
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["url"] == "https://example.com"
        assert data["platform"] is not None
