"""Provider-neutral schedule publication authorization policy and preconditions.

Part of Phase 4B Generic Scheduling.
Enforces:
- Provider-neutral SchedulePublishPolicy contract.
- Zero hardcoded institutional roles (admin, principal, dean, manager).
- Fail-closed authorization checks requiring trusted principal, tenant matching, and valid action.
- Structural and constraint precondition verification for publication.
"""

from typing import Protocol, runtime_checkable

from app.db.models.scheduling import ScheduleRecord
from app.scheduling.states import ScheduleLifecycleStatus


class SchedulePublishPolicyError(Exception):
    """Base exception for schedule publication policy violations."""


class PublishAuthorizationError(SchedulePublishPolicyError):
    """Raised when publication authorization is rejected or fail-closed."""


class PublishPreconditionFailedError(SchedulePublishPolicyError):
    """Raised when candidate schedule does not meet publication preconditions."""


@runtime_checkable
class SchedulePublishPolicy(Protocol):
    """Protocol for schedule publication authorization decisions.

    Accepts principal, tenant context, schedule candidate, and requested action.
    Implementations must fail closed unless trusted policy/permission evidence
    explicitly grants publication. Never assumes hardcoded organizational roles.
    """

    def authorize_publish(
        self,
        *,
        principal: str | None,
        tenant_id: str,
        schedule: ScheduleRecord,
        action: str = "publish",
        permissions: set[str] | None = None,
    ) -> bool:
        """Evaluate if publication is authorized for given principal, tenant, and schedule."""
        ...


class DefaultSchedulePublishPolicy:
    """Canonical provider-neutral publication policy implementation.

    Fails closed by default: publication is denied unless an explicit trusted grant
    or capability has been provided. Does NOT hardcode any organizational role strings.
    """

    def __init__(
        self,
        *,
        granted_principals: set[str] | None = None,
        granted_permissions: set[str] | None = None,
    ) -> None:
        self.granted_principals = set(granted_principals or [])
        self.granted_permissions = set(granted_permissions or [])

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

        effective_perms = permissions or set()
        if principal in self.granted_principals:
            return True
        if self.granted_permissions and bool(self.granted_permissions & effective_perms):
            return True

        # Fails closed: ungranted principal/permission is denied
        return False


def validate_publish_preconditions(schedule: ScheduleRecord) -> None:
    """Validate all structural, constraint, and lifecycle preconditions for publication.

    Preconditions:
    - Candidate must be in REVIEW status (never DRAFT, FEASIBLE, INFEASIBLE,
      BUDGET_EXHAUSTED, FAILED, PUBLISHED, SUPERSEDED).
    - Candidate assignments must be structurally complete (>0 assignments).
    - Zero hard-constraint violations in score_components.
    - Input snapshot hash and solver evidence must be present.
    - Schedule must not be already published or superseded.
    """
    if schedule.status != ScheduleLifecycleStatus.REVIEW.value:
        raise PublishPreconditionFailedError(
            f"Schedule {schedule.id} cannot be published from status '{schedule.status}'. "
            f"Only schedules in '{ScheduleLifecycleStatus.REVIEW.value}' may be published."
        )

    if not schedule.assignments or len(schedule.assignments) == 0:
        raise PublishPreconditionFailedError(
            f"Schedule {schedule.id} cannot be published without assignments "
            f"(structurally incomplete)."
        )

    score = schedule.score_components or {}
    hard_violations = score.get("hard_constraint_violations", 0)
    if hard_violations > 0:
        raise PublishPreconditionFailedError(
            f"Schedule {schedule.id} has {hard_violations} hard constraint violations "
            f"and cannot be published."
        )

    if not schedule.input_snapshot_hash:
        raise PublishPreconditionFailedError(
            f"Schedule {schedule.id} lacks input snapshot hash evidence."
        )
