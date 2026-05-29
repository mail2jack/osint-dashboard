"""Locust load test for the OSINT dashboard."""

from locust import HttpUser, task, between


class DashboardUser(HttpUser):
    wait_time = between(1, 3)

    @task
    def health_check(self):
        self.client.get("/health?quick=1")

    @task(2)
    def api_version(self):
        self.client.get("/api/version")

    @task(3)
    def api_config(self):
        self.client.get("/api/config")

    def on_start(self):
        self.client.get("/")
