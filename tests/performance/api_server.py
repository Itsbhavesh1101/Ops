"""Canonical live API process entrypoint for REAL_LOCAL_HTTP_BENCHMARK.

Executes the standard app.main application on localhost:8008 with
E7_MOUNT_DOMAIN_ROUTERS=true and pre-registered multi-tenant benchmark credentials.
"""

import os
import sys
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Enable domain routes composition
os.environ["E7_MOUNT_DOMAIN_ROUTERS"] = "true"

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402
from tests.performance.fixtures import register_perf_principal  # noqa: E402

# Pre-register synthetic benchmark tenant credentials
for t_id in [
    "perf-tenant-01",
    "perf-tenant-02",
    "perf-tenant-03",
    "perf-tenant-04",
    "perf-tenant-05",
]:
    register_perf_principal(tenant_id=t_id, role="planner")
    register_perf_principal(tenant_id=t_id, role="operator")
    register_perf_principal(tenant_id=t_id, role="auditor")

if __name__ == "__main__":
    port = int(os.environ.get("PERF_API_PORT", "8008"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
