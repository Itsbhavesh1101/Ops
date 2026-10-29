from __future__ import annotations

import os
from pathlib import Path

import pytest

RECOVERY_TEST_ROOT = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Require an explicit opt-in before executing infrastructure recovery drills."""
    if os.environ.get("E7_RUN_RECOVERY") == "1":
        return
    marker = pytest.mark.skip(
        reason="Recovery drills require E7_RUN_RECOVERY=1 and provisioned infrastructure"
    )
    for item in items:
        if item.path.resolve().is_relative_to(RECOVERY_TEST_ROOT):
            item.add_marker(marker)
