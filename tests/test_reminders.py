class TestReminderCreate:
    URL = "/cms/reminders/create"
    CREATE_BODY = {
        "title": "Test Reminder",
        "description": "A reminder description",
        "reminder_date": "2026-06-15T10:00:00",
        "due_date": "2026-06-15",
        "priority": "high",
    }

    def test_requires_auth(self, client):
        resp = client.post(self.URL, json=self.CREATE_BODY)
        assert resp.status_code == 401

    def test_create_reminder(self, auth_client, db_session):
        resp = auth_client.post(self.URL, json=self.CREATE_BODY)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["reminder"]["title"] == "Test Reminder"
        assert data["reminder"]["priority"] == "high"

    def test_create_empty_title(self, auth_client):
        resp = auth_client.post(self.URL, json={})
        assert resp.status_code == 400

    def test_create_with_case(self, auth_client, db_session):
        client_resp = auth_client.post(
            "/cms/clients/create",
            json={
                "name": "Reminder Client",
                "is_company": True,
            },
        )
        client_id = client_resp.get_json()["client"]["id"]
        case_resp = auth_client.post(
            "/cms/cases/create",
            json={
                "title": "Reminder Case",
                "client_id": client_id,
            },
        )
        case_id = case_resp.get_json()["case"]["id"]
        resp = auth_client.post(
            self.URL,
            json={
                **self.CREATE_BODY,
                "case_id": case_id,
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["reminder"]["case_id"] == case_id

    def test_create_low_priority(self, auth_client, db_session):
        resp = auth_client.post(
            self.URL,
            json={
                **self.CREATE_BODY,
                "priority": "low",
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["reminder"]["priority"] == "low"

    def test_create_recurring(self, auth_client, db_session):
        resp = auth_client.post(
            self.URL,
            json={
                **self.CREATE_BODY,
                "recurrence": "daily",
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["reminder"]["recurrence"] == "daily"


class TestReminderList:
    def test_list_reminders(self, auth_client, db_session):
        auth_client.post(
            "/cms/reminders/create",
            json={
                "title": "List Test",
                "description": "Found in list",
                "reminder_date": "2026-06-15T10:00:00",
            },
        )
        resp = auth_client.get("/cms/reminders")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "List Test" in html

    def test_filter_overdue(self, auth_client, db_session):
        auth_client.post(
            "/cms/reminders/create",
            json={
                "title": "Overdue Item",
                "description": "Overdue",
                "reminder_date": "2025-01-01T10:00:00",
                "due_date": "2025-01-01",
            },
        )
        resp = auth_client.get("/cms/reminders?filter=overdue")
        assert resp.status_code == 200

    def test_filter_mine(self, auth_client, db_session):
        resp = auth_client.get("/cms/reminders?filter=mine")
        assert resp.status_code == 200


class TestReminderComplete:
    def _create_reminder(self, auth_client):
        resp = auth_client.post(
            "/cms/reminders/create",
            json={
                "title": "Complete Me",
                "description": "Will be completed",
                "reminder_date": "2026-06-15T10:00:00",
            },
        )
        return resp.get_json()["reminder"]["id"]

    def test_complete_reminder(self, auth_client, db_session):
        from cms.models import Reminder

        reminder_id = self._create_reminder(auth_client)
        resp = auth_client.post(f"/cms/reminders/{reminder_id}/complete", json={})
        assert resp.status_code == 200
        reminder = db_session.get(Reminder, reminder_id)
        assert reminder.is_completed is True

    def test_complete_nonexistent(self, auth_client):
        resp = auth_client.post("/cms/reminders/nonexistent/complete", json={})
        assert resp.status_code == 404


class TestReminderSnooze:
    def _create_reminder(self, auth_client):
        resp = auth_client.post(
            "/cms/reminders/create",
            json={
                "title": "Snooze Me",
                "description": "Will be snoozed",
                "reminder_date": "2026-06-15T10:00:00",
            },
        )
        return resp.get_json()["reminder"]["id"]

    def test_snooze_reminder(self, auth_client, db_session):
        from cms.models import Reminder

        reminder_id = self._create_reminder(auth_client)
        reminder = db_session.get(Reminder, reminder_id)
        old_date = reminder.reminder_date
        resp = auth_client.post(
            f"/cms/reminders/{reminder_id}/snooze?minutes=60", json={}
        )
        assert resp.status_code == 200
        db_session.refresh(reminder)
        assert reminder.reminder_date > old_date

    def test_snooze_nonexistent(self, auth_client):
        resp = auth_client.post("/cms/reminders/nonexistent/snooze", json={})
        assert resp.status_code == 404


class TestReminderDelete:
    def _create_reminder(self, auth_client):
        resp = auth_client.post(
            "/cms/reminders/create",
            json={
                "title": "Delete Me",
                "description": "Will be deleted",
                "reminder_date": "2026-06-15T10:00:00",
            },
        )
        return resp.get_json()["reminder"]["id"]

    def test_delete_reminder(self, auth_client, db_session):
        from cms.models import Reminder

        reminder_id = self._create_reminder(auth_client)
        resp = auth_client.post(f"/cms/reminders/{reminder_id}/delete", json={})
        assert resp.status_code == 200
        reminder = db_session.get(Reminder, reminder_id)
        assert reminder.is_deleted is True

    def test_delete_nonexistent(self, auth_client):
        resp = auth_client.post("/cms/reminders/nonexistent/delete", json={})
        assert resp.status_code == 404


class TestReminderOverdueCheck:
    def test_check_overdue(self, auth_client, db_session):
        auth_client.post(
            "/cms/reminders/create",
            json={
                "title": "Overdue Check",
                "description": "Testing overdue detection",
                "reminder_date": "2025-01-01T10:00:00",
            },
        )
        resp = auth_client.get("/cms/api/reminders/check-overdue")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "overdue_count" in data


class TestReminderView:
    def test_view_reminder(self, auth_client, db_session):
        create_resp = auth_client.post(
            "/cms/reminders/create",
            json={
                "title": "Viewable Reminder",
                "description": "Seen on detail page",
                "reminder_date": "2026-06-15T10:00:00",
            },
        )
        reminder_id = create_resp.get_json()["reminder"]["id"]
        resp = auth_client.get(f"/cms/reminders/{reminder_id}")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Viewable Reminder" in html

    def test_view_nonexistent(self, auth_client):
        resp = auth_client.get("/cms/reminders/nonexistent")
        assert resp.status_code == 404
