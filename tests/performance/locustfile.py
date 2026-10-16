"""Locust performance load test scenario for REAL_LOCAL_HTTP_BENCHMARK.

Executes representative HTTP reads and mutations against the canonical live local API:
- Health and readiness probes
- Authenticated multi-tenant goal creations
- Authenticated audit trail searches
- Authenticated operations analytics telemetry
"""

import random

from locust import HttpUser, between, task

from tests.performance.fixtures import PERF_SECRET_SALT


class E7ApiUser(HttpUser):
    """Simulates mixed multi-tenant operations activity against the live local API."""

    wait_time = between(0.01, 0.05)

    def on_start(self) -> None:
        self.tenants = [
            "perf-tenant-01",
            "perf-tenant-02",
            "perf-tenant-03",
            "perf-tenant-04",
            "perf-tenant-05",
        ]
        self.tenant_id = random.choice(self.tenants)
        role = "planner"
        cred_id = f"perf_{self.tenant_id.replace('-', '_')}_{role[:4]}"
        raw_secret = f"{PERF_SECRET_SALT[:35]}_{role[:4]}_{self.tenant_id[-4:]}"
        if len(raw_secret) < 43:
            raw_secret = raw_secret.ljust(45, "x")
        self.api_key = f"e7sk_{cred_id}.{raw_secret}"
        self.headers = {
            "X-E7-Service-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @task(3)
    def probe_health(self) -> None:
        self.client.get("/api/v1/health")

    @task(3)
    def probe_ready(self) -> None:
        self.client.get("/api/v1/ready")

    @task(2)
    def create_goal(self) -> None:
        payload = {
            "objective": f"Locust Academic Goal for {self.tenant_id}",
            "scope": "Phase 8C Real HTTP Load",
        }
        self.client.post("/api/v1/goals", json=payload, headers=self.headers)

    @task(2)
    def search_audit(self) -> None:
        self.client.get("/api/v1/audit?limit=25", headers=self.headers)

    @task(2)
    def get_analytics(self) -> None:
        self.client.get("/api/v1/analytics/operations", headers=self.headers)
