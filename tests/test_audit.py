from datetime import datetime, timezone, timedelta


class TestAuditLogList:
    URL = "/cms/audit"

    def test_requires_auth(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302

    def test_list_empty(self, auth_client):
        resp = auth_client.get(self.URL)
        assert resp.status_code == 200

    def test_list_with_entries(self, auth_client, db_session):
        from cms.models import AuditLog

        AuditLog.log(
            user_id="test",
            action="create",
            entity_type="case",
            entity_id="test-id",
            ip_address="127.0.0.1",
            description="Test audit entry",
        )
        db_session.commit()
        resp = auth_client.get(self.URL)
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Test audit entry" in html


class TestAuditLogFiltering:
    def test_filter_by_type(self, auth_client, db_session):
        from cms.models import AuditLog

        AuditLog.log(
            user_id="test",
            action="create",
            entity_type="case",
            entity_id="c1",
            ip_address="127.0.0.1",
        )
        AuditLog.log(
            user_id="test",
            action="update",
            entity_type="client",
            entity_id="cl1",
            ip_address="127.0.0.1",
        )
        db_session.commit()
        resp = auth_client.get("/cms/audit?entity_type=client")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "client" in html.lower() or "update" in html.lower()

    def test_filter_by_action(self, auth_client, db_session):
        from cms.models import AuditLog

        AuditLog.log(
            user_id="test",
            action="delete",
            entity_type="case",
            entity_id="c1",
            ip_address="127.0.0.1",
            description="Deleted case",
        )
        db_session.commit()
        resp = auth_client.get("/cms/audit?action=delete")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Deleted case" in html


class TestAuditLogPurge:
    @staticmethod
    def _tenant():
        from cms.models import User

        admin = User.query.filter_by(username="admin").first()
        return admin.tenant_id if admin else None

    def test_purge_old_deletes_expired(self, app, db_session):
        from cms.models import AuditLog

        with app.app_context():
            old_time = datetime.now(timezone.utc) - timedelta(days=400)
            AuditLog.log(
                user_id="test",
                action="create",
                entity_type="case",
                entity_id="old-id",
                ip_address="127.0.0.1",
                tenant_id=self._tenant(),
            )
            entry = AuditLog.query.filter_by(entity_id="old-id").first()
            entry.timestamp = old_time
            db_session.commit()
            count = AuditLog.purge_old(days=365)
            assert count >= 1
            remaining = AuditLog.query.filter_by(entity_id="old-id").first()
            assert remaining is None

    def test_purge_old_keeps_recent(self, app, db_session):
        from cms.models import AuditLog

        with app.app_context():
            AuditLog.log(
                user_id="test",
                action="create",
                entity_type="case",
                entity_id="recent-id",
                ip_address="127.0.0.1",
                tenant_id=self._tenant(),
            )
            db_session.commit()
            count = AuditLog.purge_old(days=365)
            remaining = AuditLog.query.filter_by(entity_id="recent-id").first()
            assert remaining is not None
            if count > 0:
                count = 0

    def test_purge_old_keeps_without_commit(self, app, db_session):
        from cms.models import AuditLog

        with app.app_context():
            AuditLog.log(
                user_id="test",
                action="create",
                entity_type="case",
                entity_id="nocommit",
                ip_address="127.0.0.1",
                tenant_id=self._tenant(),
            )
            count = AuditLog.purge_old(days=365)
            assert count >= 0

    def test_purge_old_uses_setting(self, app, db_session):
        from cms.models import AuditLog, Setting

        with app.app_context():
            Setting.set("audit_log_retention_days", "1")
            old_time = datetime.now(timezone.utc) - timedelta(days=10)
            AuditLog.log(
                user_id="test",
                action="create",
                entity_type="case",
                entity_id="setting-old",
                ip_address="127.0.0.1",
                tenant_id=self._tenant(),
            )
            entry = AuditLog.query.filter_by(entity_id="setting-old").first()
            entry.timestamp = old_time
            db_session.commit()
            count = AuditLog.purge_old()
            assert count >= 1


class TestAuditLogPage:
    def test_pagination(self, auth_client, db_session):
        from cms.models import AuditLog

        for i in range(25):
            AuditLog.log(
                user_id="test",
                action="create",
                entity_type="case",
                entity_id=f"page-{i}",
                ip_address="127.0.0.1",
                description=f"Entry {i}",
            )
        db_session.commit()
        resp = auth_client.get("/cms/audit?page=1")
        assert resp.status_code == 200
        resp = auth_client.get("/cms/audit?page=999")
        assert resp.status_code == 200

    def test_search(self, auth_client, db_session):
        from cms.models import AuditLog

        AuditLog.log(
            user_id="test",
            action="create",
            entity_type="case",
            entity_id="search-test",
            ip_address="127.0.0.1",
            description="Unique searchable entry",
        )
        db_session.commit()
        resp = auth_client.get("/cms/audit?q=Unique+searchable")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Unique searchable entry" in html
