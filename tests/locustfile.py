"""
Locust load test for the OSINT dashboard.

Run locally:
    locust -f tests/locustfile.py --headless -u 10 -r 2 --run-time 30s

Run with thresholds:
    locust -f tests/locustfile.py --headless -u 10 -r 2 --run-time 30s \
        --html locust_report.html --csv locust_report

CI thresholds:
    locust -f tests/locustfile.py --headless -u 5 -r 1 --run-time 20s \
        --html locust_report.html --stop-timeout 10 && echo "OK"

Expected (5 users, 20s):
  - Median response time < 100ms (p95 < 300ms)
  - Error rate < 1%
  - Requests/s > 20
"""

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
