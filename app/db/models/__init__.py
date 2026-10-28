"""app.db.models package — Canonical entity model registration.

Registers all persistence models with Base.metadata.
In Task 1I.4B, active models are strictly:
- AuditEventRecord (audit_events)
- OutboxEventRecord (outbox_events)
- InboxMessageRecord (inbox_messages)
- IdempotencyRecord (idempotency_records)
- JobModel (jobs)
"""

from app.audit.models import AuditEventRecord
from app.core.idempotency.models import IdempotencyRecord
from app.core.jobs.models import JobModel
from app.db.models.notification_outbox import NotificationOutboxCommandRecord
from app.db.models.planning import (
    GoalRecord,
    MilestoneRecord,
    PlanRecord,
    TaskDependencyRecord,
    TaskRecord,
    TaskRiskRecord,
)
from app.db.models.scheduling import (
    CalendarLinkRecord,
    CalendarWatchChannelRecord,
    ConstraintDefinitionRecord,
    ResourceAvailabilityRecord,
    ResourceRecord,
    ScheduleAssignmentRecord,
    ScheduleJobRecord,
    ScheduleRecord,
    SolverProfileRecord,
)
from app.db.models.service_credential import ServiceApiKeyCredentialRecordModel
from app.db.models.sla import SlaPolicyRecord, TaskSlaStateRecord
from app.db.models.workflow import (
    WorkflowArtifactRecord,
    WorkflowDefinitionRecord,
    WorkflowInstanceRecord,
    WorkflowTaskMappingRecord,
)
from app.events.inbox.models import InboxMessageRecord
from app.events.outbox.models import OutboxEventRecord

__all__ = [
    "AuditEventRecord",
    "CalendarLinkRecord",
    "CalendarWatchChannelRecord",
    "ConstraintDefinitionRecord",
    "GoalRecord",
    "IdempotencyRecord",
    "InboxMessageRecord",
    "JobModel",
    "MilestoneRecord",
    "NotificationOutboxCommandRecord",
    "OutboxEventRecord",
    "PlanRecord",
    "ResourceAvailabilityRecord",
    "ResourceRecord",
    "ScheduleAssignmentRecord",
    "ScheduleJobRecord",
    "ScheduleRecord",
    "ServiceApiKeyCredentialRecordModel",
    "SlaPolicyRecord",
    "SolverProfileRecord",
    "TaskDependencyRecord",
    "TaskRecord",
    "TaskRiskRecord",
    "TaskSlaStateRecord",
    "WorkflowArtifactRecord",
    "WorkflowDefinitionRecord",
    "WorkflowInstanceRecord",
    "WorkflowTaskMappingRecord",
]
