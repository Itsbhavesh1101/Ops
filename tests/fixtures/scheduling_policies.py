"""Test fixture authorization policies for scheduling domain tests.

These policies exist strictly for test isolation and must never be placed in production app code.
"""

from app.db.models.scheduling import ScheduleRecord


class AllowAllPublishPolicy:
    """Deterministic test fixture policy granting publication to valid matching principals."""

    def authorize_publish(
        self,
        *,
        principal: str | None,
        tenant_id: str,
        schedule: ScheduleRecord,
        action: str = "publish",
        permissions: set[str] | None = None,
    ) -> bool:
        if not principal or not principal.strip():
            return False
        if not tenant_id or not tenant_id.strip():
            return False
        if schedule.tenant_id != tenant_id:
            return False
        if action != "publish":
            return False
        return True


class DenyAllPublishPolicy:
    """Deterministic test fixture policy denying publication under all circumstances."""

    def authorize_publish(
        self,
        *,
        principal: str | None,
        tenant_id: str,
        schedule: ScheduleRecord,
        action: str = "publish",
        permissions: set[str] | None = None,
    ) -> bool:
        return False
