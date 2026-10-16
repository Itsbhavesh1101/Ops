"""Pytest configuration and shared fixtures for live scheduling database integration tests.

Requires a running Docker PostgreSQL container and the opt-in flag:
    E7_RUN_SCHEDULING_INTEGRATION=1 or E7_RUN_POSTGRES_INTEGRATION=1

When the flag is not set, live database integration tests are safely skipped.
Subdirectory tests (e.g. education benchmark) maintain their own independent opt-in gates.
"""

import os

import pytest


def is_scheduling_integration_enabled() -> bool:
    """Check if scheduling live database integration tests are explicitly enabled."""
    return (
        os.environ.get("E7_RUN_SCHEDULING_INTEGRATION") == "1"
        or os.environ.get("E7_RUN_POSTGRES_INTEGRATION") == "1"
    )


@pytest.fixture(autouse=True)
def check_scheduling_integration_opt_in(request: pytest.FixtureRequest) -> None:
    """Skip live database tests if opt-in flag is not set."""
    # Never skip education tests (they use E7_RUN_EDUCATION_SOLVER_BENCHMARK)
    if "education" in str(request.fspath):
        return

    if not is_scheduling_integration_enabled():
        pytest.skip(
            "Live scheduling database integration tests require "
            "E7_RUN_SCHEDULING_INTEGRATION=1 or E7_RUN_POSTGRES_INTEGRATION=1"
        )
