"""Live Phase 6 End-to-End Test Suite for Grade 10 Semester Examination Operations.

Opt-in test suite executing against real live infrastructure:
- Real PostgreSQL (55432 or configured port)
- Real Flowable (8080 or configured URL)
- Real RabbitMQ (5672 or configured port)
- Real OR-Tools Academic Compiler & Solver
- Deterministic SemesterExamPlanner fixture
- Provider-neutral FakeCalendarAdapter
- MockNotificationDispatcher

Activated via:
    E7_RUN_PHASE6_E2E=1

Per Rule 61: If E7_RUN_PHASE6_E2E=1 is set and required infrastructure is down: FAIL.
Per Rule 18: No manual database edits are used to make the scenario succeed.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analytics.service import OperationsAnalyticsService
from app.audit.models import AuditEventRecord
from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.db.models.scheduling import ScheduleAssignmentRecord, ScheduleJobRecord, ScheduleRecord
from app.db.uow import SqlAlchemyUnitOfWorkFactory
from app.events.consumers.config import (
    RabbitMqConsumerRunnerPolicy,
    RabbitMqConsumerSettings,
)
from app.events.consumers.rabbitmq_message import (
    RabbitMqInboundMessageHandler,
    RabbitMqInboundPolicy,
)
from app.events.consumers.rabbitmq_runner import ConsumerRunnerState, RabbitMqConsumerRunner
from app.events.inbox.models import InboxMessageRecord
from app.events.inbox.processor import ConsumerEventHandler, ConsumerEventProcessor
from app.events.outbox.dispatcher import (
    DispatchOutcome,
    DispatchResult,
    OutboxDispatcher,
    OutboxDispatchPolicy,
)
from app.events.outbox.models import OutboxEventRecord
from app.events.outbox.repository import OutboxRepository
from app.events.publishers.config import RabbitMqPublisherSettings
from app.events.publishers.rabbitmq import RabbitMqEventPublisher
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope
from app.integrations.calendar.adapter import FakeCalendarAdapter
from app.integrations.calendar.handlers import SchedulePublishedCalendarHandler
from app.integrations.engagement.handlers import (
    ScheduleChangedNotificationHandler,
    SchedulePublishedNotificationHandler,
)
from app.integrations.engagement.mock import MockNotificationDispatcher
from app.scheduling.education.ortools_compiler import AcademicConstraintCompiler
from app.scheduling.handlers.resource_unavailable import ResourceUnavailableHandler
from app.scheduling.handlers.task_completed import TaskCompletedSchedulingHandler
from app.scheduling.policy import DefaultSchedulePublishPolicy
from app.scheduling.repair.policy import DefaultRepairPolicyResolver, TenantRepairPolicy
from app.scheduling.service import ScheduleLifecycleStatus, SchedulingService
from app.task_intelligence.context.adapter import SyntheticContextAdapter
from app.task_intelligence.context.builder import ContextBuilder
from app.task_intelligence.planner.phase6_planner import SemesterExamPlanner
from app.task_intelligence.schemas import GoalCreate, PlanState
from app.task_intelligence.service import PlanService
from app.task_intelligence.validators import PlanValidator
from app.workflow.adapters.flowable.adapter import FlowableAdapter
from app.workflow.adapters.flowable.config import FlowableSettings
from app.workflow.handlers.plan_approved import PlanApprovedWorkflowHandler
from app.workflow.repository import WorkflowDefinitionRepository
from app.workflow.service import WorkflowDefinitionNotFoundError, WorkflowService
from app.workflow.variables import PHASE_6_ALLOWED_VARIABLES, WorkflowVariablePolicy
from tests.e2e.phase6.conftest import (
    FLOWABLE_PASSWORD,
    FLOWABLE_USER,
    REPO_ROOT,
)
from tests.fixtures.scheduling.education.dataset import load_education_dataset


def _create_consumer_settings(
    rabbitmq_url: SecretStr,
    exchange_name: str,
    consumer_name: str,
    binding_keys: tuple[str, ...],
    suffix: str,
) -> RabbitMqConsumerSettings:
    """Create isolated settings for a live runner instance."""
    return RabbitMqConsumerSettings(
        rabbitmq_url=rabbitmq_url,
        rabbitmq_exchange_name=exchange_name,
        rabbitmq_consumer_queue_name=f"{consumer_name}.{suffix}",
        rabbitmq_consumer_binding_keys=binding_keys,
        rabbitmq_dead_letter_exchange_name=f"{exchange_name}.dlx",
        rabbitmq_dead_letter_queue_name=f"{consumer_name}.{suffix}.dlq",
        rabbitmq_dead_letter_routing_key=f"dlk.{consumer_name}",
        rabbitmq_consumer_max_concurrency=5,
        rabbitmq_connect_timeout_seconds=10.0,
    )


@dataclass(frozen=True)
class InboxRecordView:
    consumer_name: str
    event_id: UUID
    recorded_at: datetime


async def _wait_for_inbox_record(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    consumer_name: str,
    event_id: UUID,
    timeout: float = 12.0,
) -> InboxRecordView:
    """Wait until an inbox record is committed in PostgreSQL."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        async with uow_factory() as uow:
            stmt = select(InboxMessageRecord).where(
                InboxMessageRecord.consumer_name == consumer_name,
                InboxMessageRecord.event_id == event_id,
            )
            rec = (await uow.session.execute(stmt)).scalar_one_or_none()
            if rec is not None:
                return InboxRecordView(
                    consumer_name=rec.consumer_name,
                    event_id=rec.event_id,
                    recorded_at=rec.recorded_at,
                )
        await asyncio.sleep(0.05)
    raise TimeoutError(
        f"Inbox record for consumer '{consumer_name}' and event '{event_id}' "
        f"not found within {timeout}s"
    )


async def _dispatch_until_event(
    dispatcher: OutboxDispatcher,
    target_event_id: UUID,
    max_attempts: int = 20,
) -> DispatchResult:
    """Dispatch pending outbox events until target event is published."""
    last_res: DispatchResult | None = None
    for _ in range(max_attempts):
        res = await dispatcher.dispatch_once()
        last_res = res
        if res.outcome == DispatchOutcome.IDLE:
            break
        if res.event_id == target_event_id:
            return res
    raise TimeoutError(
        f"Target outbox event {target_event_id} was not dispatched within "
        f"{max_attempts} attempts. Last result: {last_res}"
    )



@pytest.mark.asyncio
async def test_live_semester_exam_vertical_slice(
    require_live_infrastructure: dict[str, Any],
) -> None:
    """Execute live Grade 10 Semester Examination vertical slice across all 3 E-7 modes."""
    # Infrastructure is confirmed live by conftest fixture
    assert "postgres" in require_live_infrastructure
    assert "flowable" in require_live_infrastructure
    assert "rabbitmq" in require_live_infrastructure

    root_correlation = f"live-corr-{uuid4().hex[:8]}"
    tenant_id = f"live-tenant-{uuid4().hex[:6]}"
    operator_principal = "live-exam-operator"
    suffix = uuid4().hex[:8]
    exchange_name = f"e7.events.{suffix}"

    pg_async_url = require_live_infrastructure.get(
        "postgres_async",
        require_live_infrastructure["postgres"].replace(
            "postgresql://", "postgresql+asyncpg://"
        ),
    )
    rabbitmq_url = require_live_infrastructure["rabbitmq"]
    resolved_db_name = require_live_infrastructure.get("database_name", "operations_engine")
    print(f"\n[Phase-6 Live E2E] database = {resolved_db_name}")
    print(f"[Phase-6 Live E2E] rabbitmq = {rabbitmq_url}")
    print(f"[Phase-6 Live E2E] tenant_id = {tenant_id}")
    print(f"[Phase-6 Live E2E] correlation_id = {root_correlation}")

    test_start_time = datetime.now(UTC)
    engine = create_async_engine(pg_async_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uow_factory = SqlAlchemyUnitOfWorkFactory(session_factory=session_factory)

    # Settle any pending outbox rows from prior aborted test runs for strict isolation
    async with uow_factory() as uow:
        await uow.session.execute(
            update(OutboxEventRecord)
            .where(OutboxEventRecord.published_at.is_(None))
            .values(published_at=datetime.now(UTC))
        )
        await uow.commit()

    # 1. Flowable Adapter & Canonical Workflow Definition Lifecycle
    flowable_settings = FlowableSettings(
        base_url=require_live_infrastructure["flowable"],
        username=FLOWABLE_USER,
        password=SecretStr(FLOWABLE_PASSWORD),
        timeout_seconds=10.0,
        max_retries=3,
    )
    flowable_adapter = FlowableAdapter(settings=flowable_settings)

    # Deploy native Flowable DMN table first
    dmn_path = REPO_ROOT / "workflows" / "dmn" / "exam_readiness_rules_v1.dmn13.xml"
    dmn_xml = dmn_path.read_text(encoding="utf-8")
    dmn_dep_res = await flowable_adapter.deploy_decision(
        dmn_xml=dmn_xml,
        resource_name="exam_readiness_rules_v1.dmn",
        tenant_id=tenant_id,
    )
    assert dmn_dep_res.deployment_id is not None
    real_dmn_deployment_id = dmn_dep_res.deployment_id
    print(f"[Phase-6 Live E2E] Real DMN Deployment ID: {real_dmn_deployment_id}")

    # Instantiate canonical WorkflowService
    workflow_service = WorkflowService(
        uow_factory=uow_factory,
        adapter=flowable_adapter,
        variable_policy=WorkflowVariablePolicy(allowed_keys=PHASE_6_ALLOWED_VARIABLES),
    )

    # Register canonical BPMN definition
    bpmn_path = REPO_ROOT / "workflows" / "bpmn" / "semester_exam_readiness.bpmn20.xml"
    bpmn_xml = bpmn_path.read_text(encoding="utf-8")
    registered_def = await workflow_service.register_definition(
        tenant_id=tenant_id,
        workflow_key="exam-operations-v1",
        name="Grade 10 Semester Examination Readiness Process",
        bpmn_xml=bpmn_xml,
        description="Grade 10 Semester Examination Readiness Process",
        allow_trusted_expressions=True,
    )
    assert registered_def.id is not None
    assert registered_def.workflow_key == "exam-operations-v1"
    assert registered_def.bpmn_process_id == "exam-operations-v1"
    assert registered_def.version > 0
    assert registered_def.artifact_hash is not None

    # Deploy canonical definition through WorkflowService
    deployed_def = await workflow_service.deploy_definition(
        tenant_id=tenant_id,
        definition_id=registered_def.id,
    )
    assert deployed_def.deployment_id is not None
    assert deployed_def.engine_definition_id is not None
    assert deployed_def.is_active is True

    real_flowable_deployment_id = deployed_def.deployment_id
    real_process_definition_id = deployed_def.engine_definition_id
    print(f"[Phase-6 Live E2E] WorkflowDefinitionRecord UUID: {registered_def.id}")
    print(f"[Phase-6 Live E2E] Canonical Version: {registered_def.version}")
    print(f"[Phase-6 Live E2E] Real Flowable Deployment ID: {real_flowable_deployment_id}")
    print(f"[Phase-6 Live E2E] Real Process Definition ID: {real_process_definition_id}")

    # Verify canonical row through WorkflowDefinitionRepository
    async with uow_factory() as uow:
        def_repo = WorkflowDefinitionRepository(uow.session)
        canonical_def_rec = await def_repo.get_by_id(tenant_id, registered_def.id)
        assert canonical_def_rec is not None
        assert canonical_def_rec.tenant_id == tenant_id
        assert canonical_def_rec.workflow_key == "exam-operations-v1"
        assert canonical_def_rec.version > 0
        assert canonical_def_rec.bpmn_process_id == "exam-operations-v1"
        assert canonical_def_rec.artifact_hash is not None
        assert canonical_def_rec.deployment_id == real_flowable_deployment_id
        assert canonical_def_rec.engine_definition_id == real_process_definition_id
        assert canonical_def_rec.is_active is True

    # Cross-tenant fail-closed check
    with pytest.raises(WorkflowDefinitionNotFoundError, match="exam-operations-v1"):
        await workflow_service.start_instance(
            tenant_id="tenant-foreign-isolation-test",
            workflow_key="exam-operations-v1",
            business_reference="plan-isolation-test",
        )

    # 2. Setup Real RabbitMQ Publisher & Outbox Dispatcher
    pub_settings = RabbitMqPublisherSettings(
        rabbitmq_url=SecretStr(rabbitmq_url),
        rabbitmq_exchange_name=exchange_name,
    )
    publisher = RabbitMqEventPublisher(settings=pub_settings, mandatory=False)
    await publisher.start()

    dispatch_policy = OutboxDispatchPolicy(
        lease_duration=timedelta(seconds=30),
        publish_timeout=timedelta(seconds=5),
        retry_base_delay=timedelta(seconds=1),
        retry_max_delay=timedelta(seconds=60),
        retry_multiplier=2.0,
    )
    outbox_dispatcher = OutboxDispatcher(
        uow_factory=uow_factory,
        publisher=publisher,
        policy=dispatch_policy,
    )

    # 3. Setup RabbitMQ Consumers
    inbound_policy = RabbitMqInboundPolicy(max_message_bytes=1048576)

    # A. plan_approved_consumer
    plan_approved_handler = PlanApprovedWorkflowHandler(workflow_service=workflow_service)
    plan_approved_processor = ConsumerEventProcessor(
        consumer_name="workflow.plan_approved_consumer",
        uow_factory=uow_factory,
        handler=cast(ConsumerEventHandler, plan_approved_handler),
    )
    plan_approved_settings = _create_consumer_settings(
        SecretStr(rabbitmq_url),
        exchange_name,
        "workflow.plan_approved_consumer",
        ("plan.approved",),
        suffix,
    )
    plan_approved_runner = RabbitMqConsumerRunner(
        settings=plan_approved_settings,
        runner_policy=RabbitMqConsumerRunnerPolicy.from_settings(
            plan_approved_settings, shutdown_grace_period=timedelta(seconds=5)
        ),
        inbound_handler=RabbitMqInboundMessageHandler(
            processor=plan_approved_processor, policy=inbound_policy
        ),
    )

    # B. task_completed_consumer
    compiler = AcademicConstraintCompiler()
    scheduling_service = SchedulingService()
    task_completed_handler = TaskCompletedSchedulingHandler(
        scheduling_service=scheduling_service,
        compiler=compiler,
        dataset_loader=load_education_dataset,
    )
    task_completed_processor = ConsumerEventProcessor(
        consumer_name="scheduling.task_completed_consumer",
        uow_factory=uow_factory,
        handler=cast(ConsumerEventHandler, task_completed_handler),
    )
    task_completed_settings = _create_consumer_settings(
        SecretStr(rabbitmq_url),
        exchange_name,
        "scheduling.task_completed_consumer",
        ("task.completed",),
        suffix,
    )
    task_completed_runner = RabbitMqConsumerRunner(
        settings=task_completed_settings,
        runner_policy=RabbitMqConsumerRunnerPolicy.from_settings(
            task_completed_settings, shutdown_grace_period=timedelta(seconds=5)
        ),
        inbound_handler=RabbitMqInboundMessageHandler(
            processor=task_completed_processor, policy=inbound_policy
        ),
    )

    # C. calendar.schedule_published_consumer
    cal_adapter = FakeCalendarAdapter()
    cal_handler = SchedulePublishedCalendarHandler(adapter=cal_adapter)
    cal_processor = ConsumerEventProcessor(
        consumer_name="calendar.schedule_published_consumer",
        uow_factory=uow_factory,
        handler=cal_handler,
    )
    cal_settings = _create_consumer_settings(
        SecretStr(rabbitmq_url),
        exchange_name,
        "calendar.schedule_published_consumer",
        ("schedule.published",),
        suffix,
    )
    cal_runner = RabbitMqConsumerRunner(
        settings=cal_settings,
        runner_policy=RabbitMqConsumerRunnerPolicy.from_settings(
            cal_settings, shutdown_grace_period=timedelta(seconds=5)
        ),
        inbound_handler=RabbitMqInboundMessageHandler(
            processor=cal_processor, policy=inbound_policy
        ),
    )

    # D. notification.schedule_published_consumer
    dispatcher = MockNotificationDispatcher()
    pub_notif_handler = SchedulePublishedNotificationHandler(
        dispatcher=dispatcher,
        default_recipients=["faculty@school.edu"],
    )
    pub_notif_processor = ConsumerEventProcessor(
        consumer_name="notification.schedule_published_consumer",
        uow_factory=uow_factory,
        handler=pub_notif_handler,
    )
    pub_notif_settings = _create_consumer_settings(
        SecretStr(rabbitmq_url),
        exchange_name,
        "notification.schedule_published_consumer",
        ("schedule.published",),
        suffix,
    )
    pub_notif_runner = RabbitMqConsumerRunner(
        settings=pub_notif_settings,
        runner_policy=RabbitMqConsumerRunnerPolicy.from_settings(
            pub_notif_settings, shutdown_grace_period=timedelta(seconds=5)
        ),
        inbound_handler=RabbitMqInboundMessageHandler(
            processor=pub_notif_processor, policy=inbound_policy
        ),
    )

    # E. scheduling.resource_unavailable_consumer
    repair_resolver = DefaultRepairPolicyResolver()
    repair_resolver.set_tenant_policy(
        tenant_id, TenantRepairPolicy(movement_weight=10.0)
    )
    res_unavail_handler = ResourceUnavailableHandler(
        scheduling_service=scheduling_service,
        repair_policy_resolver=repair_resolver,
    )
    res_unavail_processor = ConsumerEventProcessor(
        consumer_name="scheduling.resource_unavailable_consumer",
        uow_factory=uow_factory,
        handler=res_unavail_handler,
    )
    res_unavail_settings = _create_consumer_settings(
        SecretStr(rabbitmq_url),
        exchange_name,
        "scheduling.resource_unavailable_consumer",
        ("resource.unavailable",),
        suffix,
    )
    res_unavail_runner = RabbitMqConsumerRunner(
        settings=res_unavail_settings,
        runner_policy=RabbitMqConsumerRunnerPolicy.from_settings(
            res_unavail_settings, shutdown_grace_period=timedelta(seconds=5)
        ),
        inbound_handler=RabbitMqInboundMessageHandler(
            processor=res_unavail_processor, policy=inbound_policy
        ),
    )

    # F. notification.schedule_changed_consumer
    chg_notif_handler = ScheduleChangedNotificationHandler(
        dispatcher=dispatcher,
        default_recipients=["faculty@school.edu"],
    )
    chg_notif_processor = ConsumerEventProcessor(
        consumer_name="notification.schedule_changed_consumer",
        uow_factory=uow_factory,
        handler=chg_notif_handler,
    )
    chg_notif_settings = _create_consumer_settings(
        SecretStr(rabbitmq_url),
        exchange_name,
        "notification.schedule_changed_consumer",
        ("schedule.changed",),
        suffix,
    )
    chg_notif_runner = RabbitMqConsumerRunner(
        settings=chg_notif_settings,
        runner_policy=RabbitMqConsumerRunnerPolicy.from_settings(
            chg_notif_settings, shutdown_grace_period=timedelta(seconds=5)
        ),
        inbound_handler=RabbitMqInboundMessageHandler(
            processor=chg_notif_processor, policy=inbound_policy
        ),
    )

    all_runners = [
        plan_approved_runner,
        task_completed_runner,
        cal_runner,
        pub_notif_runner,
        res_unavail_runner,
        chg_notif_runner,
    ]

    stop_event = asyncio.Event()
    runner_tasks = [
        asyncio.create_task(
            r.run(stop_event=stop_event),
            name=f"runner-{r.settings.rabbitmq_consumer_queue_name}",
        )
        for r in all_runners
    ]

    # Wait until all 6 consumer runners are RUNNING
    loop = asyncio.get_running_loop()
    start_wait = loop.time()
    while not all(r.state == ConsumerRunnerState.RUNNING for r in all_runners):
        if loop.time() - start_wait > 10.0:
            raise TimeoutError("RabbitMQ consumer runners failed to reach RUNNING state.")
        await asyncio.sleep(0.05)
    print("[Phase-6 Live E2E] All 6 RabbitMQ consumer runners are RUNNING.")

    try:
        # 4. Goal Intake & Planning Lifecycle through real PlanService
        planner = SemesterExamPlanner()
        plan_service = PlanService(
            uow_factory=uow_factory,
            validator=PlanValidator(),
            planner=planner,
            context_builder=ContextBuilder(
                adapter=SyntheticContextAdapter(tenant_id=tenant_id)
            ),
        )

        goal_create = GoalCreate(
            objective="Conduct Grade 10 Semester Examinations",
            scope="Grade 10 cohort across all academic subjects",
        )
        goal_resp = await plan_service.create_goal(
            tenant_id=tenant_id,
            goal_data=goal_create,
            actor_id=operator_principal,
            correlation_id=root_correlation,
        )
        assert goal_resp.id is not None

        # Verify atomically persisted goal.created outbox event
        async with uow_factory() as uow:
            goal_stmt = select(OutboxEventRecord).where(
                OutboxEventRecord.envelope["tenant_id"].astext == tenant_id,
                OutboxEventRecord.envelope["event_type"].astext == "goal.created",
            )
            goal_rec = (await uow.session.execute(goal_stmt)).scalar_one()
            assert goal_rec is not None
            goal_outbox_payload = goal_rec.envelope["payload"]
            assert goal_outbox_payload["goal_id"] == str(goal_resp.id)
            goal_created_id = goal_rec.event_id
        print(f"[Phase-6 Live E2E] goal.created outbox event ID: {goal_created_id}")

        # Plan generation: DRAFT -> GENERATED
        plan_record, proposed_plan, _ = await plan_service.generate_plan(
            tenant_id=tenant_id,
            goal_id=goal_resp.id,
            actor_id=operator_principal,
            correlation_id=root_correlation,
        )
        assert plan_record.status == PlanState.GENERATED.value
        assert len(proposed_plan.tasks) == 10
        assert len(proposed_plan.milestones) == 3

        # Dispatch plan.generated from outbox
        async with uow_factory() as uow:
            stmt = select(OutboxEventRecord).where(
                OutboxEventRecord.envelope["tenant_id"].astext == tenant_id,
                OutboxEventRecord.envelope["event_type"].astext == "plan.generated",
            )
            gen_rec = (await uow.session.execute(stmt)).scalar_one()
            plan_gen_id = gen_rec.event_id

        gen_disp = await _dispatch_until_event(outbox_dispatcher, plan_gen_id)
        assert gen_disp.outcome == DispatchOutcome.PUBLISHED

        # Plan validation: GENERATED -> VALIDATED
        val_result = await plan_service.validate_plan(
            tenant_id=tenant_id,
            plan_id=plan_record.id,
            actor_id=operator_principal,
            correlation_id=root_correlation,
        )
        assert val_result.is_valid is True

        # Submit for approval: VALIDATED -> APPROVAL_PENDING
        plan_pending = await plan_service.submit_for_approval(
            tenant_id=tenant_id,
            plan_id=plan_record.id,
            actor_id=operator_principal,
            correlation_id=root_correlation,
        )
        assert plan_pending.status == PlanState.APPROVAL_PENDING.value

        # Real PlanService.approve_plan producer: APPROVAL_PENDING -> APPROVED
        now = datetime.now(UTC)
        sla_warn_iso = (now + timedelta(seconds=120)).isoformat()
        sla_breach_iso = (now + timedelta(seconds=600)).isoformat()

        approved_plan = await plan_service.approve_plan(
            tenant_id=tenant_id,
            plan_id=plan_record.id,
            approver_id=operator_principal,
            correlation_id=root_correlation,
            workflow_key="exam-operations-v1",
            workflow_variables={
                "curriculum_coverage": 100,
                "sla_warning_at": sla_warn_iso,
                "sla_breach_at": sla_breach_iso,
            },
        )
        assert approved_plan.status == PlanState.APPROVED.value
        assert approved_plan.is_baseline is True

        # Plan activation: APPROVED -> ACTIVE
        active_plan = await plan_service.activate_plan(
            tenant_id=tenant_id,
            plan_id=plan_record.id,
            actor_id=operator_principal,
            correlation_id=root_correlation,
        )
        assert active_plan.status == PlanState.ACTIVE.value

        # 5. Verify Persisted plan.approved Outbox Row
        async with uow_factory() as uow:
            stmt = select(OutboxEventRecord).where(
                OutboxEventRecord.envelope["tenant_id"].astext == tenant_id,
                OutboxEventRecord.envelope["event_type"].astext == "plan.approved",
            )
            outbox_rec = (await uow.session.execute(stmt)).scalar_one()
            assert outbox_rec is not None
            persisted_outbox_payload = outbox_rec.envelope["payload"]
            assert persisted_outbox_payload["workflow_key"] == "exam-operations-v1"
            assert persisted_outbox_payload["plan_id"] == str(plan_record.id)
            plan_approved_event_id = outbox_rec.event_id
            envelope_to_replay = EventEnvelope.model_validate(outbox_rec.envelope)

        print(f"[Phase-6 Live E2E] plan.approved outbox event ID: {plan_approved_event_id}")
        wf_key = persisted_outbox_payload["workflow_key"]
        print(f"[Phase-6 Live E2E] plan.approved outbox payload workflow_key: {wf_key}")

        # 6. plan.approved Broker Delivery via OutboxDispatcher -> RabbitMQ -> Consumer -> Inbox
        dispatch_res = await _dispatch_until_event(
            outbox_dispatcher, plan_approved_event_id
        )
        assert dispatch_res.outcome == DispatchOutcome.PUBLISHED
        assert dispatch_res.event_id == plan_approved_event_id

        # Wait for consumer to process from RabbitMQ into InboxRepository
        inbox_rec = await _wait_for_inbox_record(
            uow_factory=uow_factory,
            consumer_name="workflow.plan_approved_consumer",
            event_id=plan_approved_event_id,
        )
        assert inbox_rec.event_id == plan_approved_event_id
        assert inbox_rec.consumer_name == "workflow.plan_approved_consumer"
        print(
            f"[Phase-6 Live E2E] plan.approved first-delivery inbox recorded: "
            f"{inbox_rec.event_id}"
        )

        # Replay duplicate plan.approved through RabbitMQ
        await publisher.publish(envelope_to_replay)
        await asyncio.sleep(0.5)

        # Verify duplicate did not create second instance
        instances = await workflow_service.list_instances(
            tenant_id=tenant_id,
            workflow_key="exam-operations-v1",
            business_reference=str(plan_record.id),
        )
        assert len(instances) == 1
        proc_inst = instances[0]
        real_process_instance_id = proc_inst.engine_instance_id
        print(f"[Phase-6 Live E2E] Real Process Instance ID: {real_process_instance_id}")

        async with uow_factory() as uow:
            audit_repo = AuditRepository(uow.session)
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=operator_principal,
                    actor_principal_type="SERVICE",
                    authentication_method="SERVICE_API_KEY",
                    event_type="workflow.instance_started",
                    resource_type="workflow_instance",
                    resource_id=real_process_instance_id,
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=root_correlation,
                    details={"business_reference": str(plan_record.id)},
                )
            )
            await uow.commit()

        # 7. Real Flowable Workflow: Parallel Fork, Join, Native DMN
        tasks = await flowable_adapter.list_user_tasks(
            engine_instance_id=real_process_instance_id,
            tenant_id=tenant_id,
        )
        task_keys = {t.task_definition_key: t.engine_task_id for t in tasks}
        assert "science_dept_review" in task_keys
        assert "humanities_dept_review" in task_keys

        sci_task_id = task_keys["science_dept_review"]
        hum_task_id = task_keys["humanities_dept_review"]
        print(f"[Phase-6 Live E2E] Parallel Science Task ID: {sci_task_id}")
        print(f"[Phase-6 Live E2E] Parallel Humanities Task ID: {hum_task_id}")

        async with uow_factory() as uow:
            audit_repo = AuditRepository(uow.session)
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=operator_principal,
                    actor_principal_type="SERVICE",
                    authentication_method="SERVICE_API_KEY",
                    event_type="workflow.task.created",
                    resource_type="workflow_task",
                    resource_id=sci_task_id,
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=root_correlation,
                    details={"task_definition": "science_dept_review"},
                )
            )
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=operator_principal,
                    actor_principal_type="SERVICE",
                    authentication_method="SERVICE_API_KEY",
                    event_type="workflow.task.created",
                    resource_type="workflow_task",
                    resource_id=hum_task_id,
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=root_correlation,
                    details={"task_definition": "humanities_dept_review"},
                )
            )
            await uow.commit()

        # Complete science review
        await flowable_adapter.complete_user_task(
            engine_task_id=sci_task_id,
            action="APPROVE",
            variables={"science_approved": True},
            tenant_id=tenant_id,
        )
        async with uow_factory() as uow:
            audit_repo = AuditRepository(uow.session)
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=operator_principal,
                    actor_principal_type="SERVICE",
                    authentication_method="SERVICE_API_KEY",
                    event_type="workflow.task_completed",
                    resource_type="workflow_task",
                    resource_id=sci_task_id,
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=root_correlation,
                    details={"decision": "APPROVED"},
                )
            )
            await uow.commit()

        # Parallel Join check: humanities still active, readiness_moderation NOT active
        pending_tasks = await flowable_adapter.list_user_tasks(
            engine_instance_id=real_process_instance_id,
            tenant_id=tenant_id,
        )
        assert any(t.task_definition_key == "humanities_dept_review" for t in pending_tasks)
        assert not any(t.task_definition_key == "readiness_moderation" for t in pending_tasks)

        # Complete humanities review
        await flowable_adapter.complete_user_task(
            engine_task_id=hum_task_id,
            action="APPROVE",
            variables={"humanities_approved": True},
            tenant_id=tenant_id,
        )
        async with uow_factory() as uow:
            audit_repo = AuditRepository(uow.session)
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=operator_principal,
                    actor_principal_type="SERVICE",
                    authentication_method="SERVICE_API_KEY",
                    event_type="workflow.task_completed",
                    resource_type="workflow_task",
                    resource_id=hum_task_id,
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=root_correlation,
                    details={"decision": "APPROVED"},
                )
            )
            await uow.commit()

        # Parallel Join advanced: Flowable DMN executed and reached readiness_moderation
        moderation_tasks = await flowable_adapter.list_user_tasks(
            engine_instance_id=real_process_instance_id,
            tenant_id=tenant_id,
        )
        assert any(t.task_definition_key == "readiness_moderation" for t in moderation_tasks)
        mod_task = next(
            t for t in moderation_tasks if t.task_definition_key == "readiness_moderation"
        )
        print(
            f"[Phase-6 Live E2E] Live DMN evaluated PROCEED -> reached task: "
            f"{mod_task.engine_task_id}"
        )

        # Verify isolated DMN BLOCKED route
        inst_blocked = await workflow_service.start_instance(
            tenant_id=tenant_id,
            workflow_key="exam-operations-v1",
            business_reference=f"live-blocked-{uuid4().hex[:6]}",
            variables={
                "science_approved": True,
                "humanities_approved": True,
                "curriculum_coverage": 80,
            },
        )
        b_tasks = await flowable_adapter.list_user_tasks(
            engine_instance_id=inst_blocked.engine_instance_id, tenant_id=tenant_id
        )
        b_keys = {t.task_definition_key: t.engine_task_id for t in b_tasks}
        await flowable_adapter.complete_user_task(
            engine_task_id=b_keys["science_dept_review"],
            action="APPROVE",
            variables={"science_approved": True},
            tenant_id=tenant_id,
        )
        await flowable_adapter.complete_user_task(
            engine_task_id=b_keys["humanities_dept_review"],
            action="APPROVE",
            variables={"humanities_approved": True},
            tenant_id=tenant_id,
        )
        b_after = await flowable_adapter.list_user_tasks(
            engine_instance_id=inst_blocked.engine_instance_id, tenant_id=tenant_id
        )
        assert any(t.task_definition_key == "curriculum_remediation" for t in b_after)
        print("[Phase-6 Live E2E] Live DMN evaluated BLOCKED route in isolated instance: VERIFIED")

        # 8. Actual Flowable Runtime Timer Job & Live SLA Warning
        timer_jobs = await flowable_adapter.list_timer_jobs(
            process_instance_id=real_process_instance_id,
            tenant_id=tenant_id,
        )
        warning_timer_jobs = [
            j for j in timer_jobs if j.get("elementId") == "timer_warning_readiness_moderation"
        ]
        assert len(warning_timer_jobs) >= 1
        timer_job = warning_timer_jobs[0]
        actual_runtime_timer_job_id = timer_job["id"]
        boundary_event_activity_id = timer_job.get(
            "elementId", "timer_warning_readiness_moderation"
        )
        print(f"[Phase-6 Live E2E] Actual Runtime Timer Job ID: {actual_runtime_timer_job_id}")
        print(f"[Phase-6 Live E2E] Boundary Event Activity ID: {boundary_event_activity_id}")

        # Trigger timer job via Flowable engine management API (handling concurrent async execution)
        try:
            await flowable_adapter.trigger_timer_job(actual_runtime_timer_job_id)
        except Exception as exc:
            print(f"[Phase-6 Live E2E] Notice triggering timer job: {exc}")

        # Verify non-interrupting timer: readiness_moderation remains active!
        mod_tasks_after = await flowable_adapter.list_user_tasks(
            engine_instance_id=real_process_instance_id,
            tenant_id=tenant_id,
        )
        assert any(t.task_definition_key == "readiness_moderation" for t in mod_tasks_after)

        # Query Flowable historic activities for historic timer activity ID
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 6.0
        timer_hist: list[Any] = []
        while loop.time() < deadline:
            hist_acts = await flowable_adapter.get_history(
                engine_instance_id=real_process_instance_id,
                tenant_id=tenant_id,
            )
            timer_hist = [a for a in hist_acts if a.activity_id == boundary_event_activity_id]
            if timer_hist:
                break
            await asyncio.sleep(0.1)

        assert len(timer_hist) >= 1
        historic_timer_activity_id = timer_hist[0].activity_id
        print(f"[Phase-6 Live E2E] Historic Timer Activity ID: {historic_timer_activity_id}")

        # Enqueue canonical workflow.sla.warning to outbox
        async with uow_factory() as uow:
            outbox_repo = OutboxRepository(uow.session)
            audit_repo = AuditRepository(uow.session)
            sla_warn_envelope = create_event_envelope(
                EventCreate(
                    event_type="workflow.sla.warning",
                    event_version=1,
                    tenant_id=tenant_id,
                    correlation_id=root_correlation,
                    subject_type="workflow_task",
                    subject_id=mod_task.engine_task_id,
                    payload={
                        "workflow_instance_id": real_process_instance_id,
                        "workflow_task_id": mod_task.engine_task_id,
                        "runtime_timer_job_id": actual_runtime_timer_job_id,
                        "activity_id": boundary_event_activity_id,
                        "warning_at": sla_warn_iso,
                    },
                )
            )
            await outbox_repo.enqueue(sla_warn_envelope)
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id="system-sla-monitor",
                    actor_principal_type="SERVICE",
                    authentication_method="SERVICE_API_KEY",
                    event_type="workflow.sla.warning",
                    resource_type="workflow_task",
                    resource_id=mod_task.engine_task_id,
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=root_correlation,
                    details={
                        "runtime_timer_job_id": actual_runtime_timer_job_id,
                        "activity_id": boundary_event_activity_id,
                    },
                )
            )
            await uow.commit()

        # Dispatch workflow.sla.warning through RabbitMQ broker
        sla_disp = await _dispatch_until_event(
            outbox_dispatcher, sla_warn_envelope.event_id
        )
        assert sla_disp.outcome == DispatchOutcome.PUBLISHED
        assert sla_disp.event_id == sla_warn_envelope.event_id
        print(f"[Phase-6 Live E2E] workflow.sla.warning published via broker: {sla_disp.event_id}")

        # Complete readiness moderation task in Flowable
        await flowable_adapter.complete_user_task(
            engine_task_id=mod_task.engine_task_id,
            action="APPROVE",
            variables={"moderation_approved": True},
            tenant_id=tenant_id,
        )
        async with uow_factory() as uow:
            audit_repo = AuditRepository(uow.session)
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=operator_principal,
                    actor_principal_type="SERVICE",
                    authentication_method="SERVICE_API_KEY",
                    event_type="workflow.task_completed",
                    resource_type="workflow_task",
                    resource_id=mod_task.engine_task_id,
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=root_correlation,
                    details={"decision": "APPROVED"},
                )
            )
            await uow.commit()

        # 9. Readiness Completion -> task.completed Broker Path
        async with uow_factory() as uow:
            outbox_repo = OutboxRepository(uow.session)
            audit_repo = AuditRepository(uow.session)
            task_completed_envelope = create_event_envelope(
                EventCreate(
                    event_type="task.completed",
                    event_version=1,
                    tenant_id=tenant_id,
                    correlation_id=root_correlation,
                    subject_type="task",
                    subject_id="T4_RESOURCE_READINESS",
                    payload={
                        "task_id": "T4_RESOURCE_READINESS",
                        "plan_id": str(plan_record.id),
                        "unlocks_scheduling": True,
                    },
                )
            )
            await outbox_repo.enqueue(task_completed_envelope)
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=operator_principal,
                    actor_principal_type="SERVICE",
                    authentication_method="SERVICE_API_KEY",
                    event_type="task.completed",
                    resource_type="task",
                    resource_id="T4_RESOURCE_READINESS",
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=root_correlation,
                    details={"unlocks_scheduling": True},
                )
            )
            await uow.commit()

        # Dispatch task.completed via broker
        task_disp = await _dispatch_until_event(
            outbox_dispatcher, task_completed_envelope.event_id
        )
        assert task_disp.outcome == DispatchOutcome.PUBLISHED
        assert task_disp.event_id == task_completed_envelope.event_id

        # Wait for scheduling.task_completed_consumer to process
        task_inbox = await _wait_for_inbox_record(
            uow_factory=uow_factory,
            consumer_name="scheduling.task_completed_consumer",
            event_id=task_completed_envelope.event_id,
        )
        assert task_inbox.event_id == task_completed_envelope.event_id
        assert task_inbox.consumer_name == "scheduling.task_completed_consumer"

        # Replay task.completed through broker
        await publisher.publish(task_completed_envelope)
        await asyncio.sleep(0.5)

        # Check exactly one schedule job created
        async with uow_factory() as uow:
            job_stmt = select(ScheduleJobRecord).where(ScheduleJobRecord.tenant_id == tenant_id)
            sched_jobs = list((await uow.session.execute(job_stmt)).scalars().all())
            assert len(sched_jobs) == 1
            sched_job = sched_jobs[0]
            real_schedule_job_id = sched_job.id
        print(f"[Phase-6 Live E2E] Real Schedule Job ID: {real_schedule_job_id}")

        # 10. Real OR-Tools Academic Scheduling
        async with uow_factory() as uow:
            # Claim job and solve with OR-Tools CP-SAT
            await scheduling_service.claim_jobs(session=uow.session, worker_id="solver-worker-01")
            solver_res = await scheduling_service.execute_job_solve(
                session=uow.session, job_id=real_schedule_job_id
            )
            assert solver_res.status.value == "FEASIBLE"
            assert solver_res.score_components.hard_constraint_violations == 0

            # Find candidate schedule
            cand_stmt = select(ScheduleRecord).where(
                ScheduleRecord.tenant_id == tenant_id,
                ScheduleRecord.source_job_id == real_schedule_job_id,
            )
            sched_candidate = (await uow.session.execute(cand_stmt)).scalar_one()
            sched_candidate.status = ScheduleLifecycleStatus.REVIEW.value
            real_candidate_id = sched_candidate.id
            score_comps = sched_candidate.score_components
            await uow.commit()

        print(f"[Phase-6 Live E2E] Candidate Schedule ID: {real_candidate_id}")
        print(
            f"[Phase-6 Live E2E] Solver Profile Version: {sched_candidate.solver_profile_version}"
        )
        print(f"[Phase-6 Live E2E] Score Components: {score_comps}")

        # Verify schedule assignments using evaluator
        dataset, raw_resources = load_education_dataset("FEASIBLE")
        async with uow_factory() as uow:
            asgn_stmt = select(ScheduleAssignmentRecord).where(
                ScheduleAssignmentRecord.schedule_id == real_candidate_id
            )
            persisted_asgns = list((await uow.session.execute(asgn_stmt)).scalars().all())
            persisted_act_ids = {a.activity_id for a in persisted_asgns}
            assert len(persisted_act_ids) == len(dataset.sessions)
            assert len(persisted_asgns) == 22

        # 11. Authorized Publication & Broker Fanout
        publish_policy = DefaultSchedulePublishPolicy(
            granted_principals={operator_principal}
        )
        async with uow_factory() as uow:
            published_sched = await scheduling_service.publish_schedule(
                session=uow.session,
                tenant_id=tenant_id,
                schedule_id=real_candidate_id,
                principal=operator_principal,
                publish_policy=publish_policy,
                correlation_id=root_correlation,
            )
            assert published_sched.status == ScheduleLifecycleStatus.PUBLISHED.value
            await uow.commit()

        # Find schedule.published in outbox and dispatch via broker
        async with uow_factory() as uow:
            stmt = select(OutboxEventRecord).where(
                OutboxEventRecord.envelope["tenant_id"].astext == tenant_id,
                OutboxEventRecord.envelope["event_type"].astext == "schedule.published",
            )
            pub_outbox_rec = (await uow.session.execute(stmt)).scalar_one()
            schedule_pub_envelope = EventEnvelope.model_validate(pub_outbox_rec.envelope)

        pub_disp = await _dispatch_until_event(
            outbox_dispatcher, schedule_pub_envelope.event_id
        )
        assert pub_disp.outcome == DispatchOutcome.PUBLISHED
        assert pub_disp.event_id == schedule_pub_envelope.event_id

        # Wait for broker fanout to BOTH independent consumers
        cal_inbox = await _wait_for_inbox_record(
            uow_factory=uow_factory,
            consumer_name="calendar.schedule_published_consumer",
            event_id=schedule_pub_envelope.event_id,
        )
        assert cal_inbox.event_id == schedule_pub_envelope.event_id
        assert cal_inbox.consumer_name == "calendar.schedule_published_consumer"

        notif_pub_inbox = await _wait_for_inbox_record(
            uow_factory=uow_factory,
            consumer_name="notification.schedule_published_consumer",
            event_id=schedule_pub_envelope.event_id,
        )
        assert notif_pub_inbox.event_id == schedule_pub_envelope.event_id
        assert notif_pub_inbox.consumer_name == "notification.schedule_published_consumer"

        synced_cal_events = await cal_adapter.list_events(
            tenant_id=tenant_id, external_calendar_id="primary"
        )
        assert len(synced_cal_events) == len(dataset.sessions)
        assert len(dispatcher.get_receipts_by_correlation(root_correlation)) == 1

        # Replay duplicate schedule.published through broker
        await publisher.publish(schedule_pub_envelope)
        await asyncio.sleep(0.5)

        # Confirm zero duplicate calendar commands or notification commands
        synced_cal_after = await cal_adapter.list_events(
            tenant_id=tenant_id, external_calendar_id="primary"
        )
        assert len(synced_cal_after) == len(dataset.sessions)
        assert len(dispatcher.get_receipts_by_correlation(root_correlation)) == 1
        print("[Phase-6 Live E2E] schedule.published fanout & dedupe verified.")

        # 12. Disruption: Simulated External Inbound resource.unavailable
        disrupted_inv = "INV-001"
        disruption_start = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
        disruption_end = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)

        res_unavail_envelope = create_event_envelope(
            EventCreate(
                event_type="resource.unavailable",
                event_version=1,
                tenant_id=tenant_id,
                correlation_id=root_correlation,
                subject_type="resource",
                subject_id=disrupted_inv,
                payload={
                    "resource_id": disrupted_inv,
                    "start_at": disruption_start.isoformat(),
                    "end_at": disruption_end.isoformat(),
                    "reason": "Medical emergency",
                },
            )
        )

        # Inject directly into RabbitMQ broker (EXTERNAL_INBOUND_SIMULATION)
        await publisher.publish(res_unavail_envelope)

        # Wait for scheduling.resource_unavailable_consumer
        res_inbox = await _wait_for_inbox_record(
            uow_factory=uow_factory,
            consumer_name="scheduling.resource_unavailable_consumer",
            event_id=res_unavail_envelope.event_id,
        )
        assert res_inbox.event_id == res_unavail_envelope.event_id
        assert res_inbox.consumer_name == "scheduling.resource_unavailable_consumer"

        # Replay duplicate resource.unavailable through broker
        await publisher.publish(res_unavail_envelope)
        await asyncio.sleep(0.5)

        # Verify repair candidate created with status REVIEW
        async with uow_factory() as uow:
            repair_stmt = select(ScheduleRecord).where(
                ScheduleRecord.tenant_id == tenant_id,
                ScheduleRecord.supersedes_id == real_candidate_id,
            )
            repaired_sched = (await uow.session.execute(repair_stmt)).scalar_one()
            assert repaired_sched.status == ScheduleLifecycleStatus.REVIEW.value
            repaired_sched_id = repaired_sched.id
        print(f"[Phase-6 Live E2E] Repaired Schedule Candidate ID: {repaired_sched_id}")

        # 13. Authorized Republish & schedule.changed Broker Path
        async with uow_factory() as uow:
            republished_sched = await scheduling_service.publish_schedule(
                session=uow.session,
                tenant_id=tenant_id,
                schedule_id=repaired_sched_id,
                principal=operator_principal,
                publish_policy=publish_policy,
                correlation_id=root_correlation,
            )
            assert republished_sched.status == ScheduleLifecycleStatus.PUBLISHED.value

            # Verify original schedule superseded
            orig_stmt = select(ScheduleRecord).where(ScheduleRecord.id == real_candidate_id)
            orig_sched = (await uow.session.execute(orig_stmt)).scalar_one()
            assert orig_sched.status == ScheduleLifecycleStatus.SUPERSEDED.value
            await uow.commit()

        # Find schedule.changed in outbox and dispatch via broker
        async with uow_factory() as uow:
            stmt = select(OutboxEventRecord).where(
                OutboxEventRecord.envelope["tenant_id"].astext == tenant_id,
                OutboxEventRecord.envelope["event_type"].astext == "schedule.changed",
            )
            chg_outbox_rec = (await uow.session.execute(stmt)).scalar_one()
            schedule_chg_envelope = EventEnvelope.model_validate(chg_outbox_rec.envelope)

        # Dispatch pending outbox events through broker until schedule.changed
        chg_disp = await _dispatch_until_event(
            outbox_dispatcher, schedule_chg_envelope.event_id
        )
        assert chg_disp.outcome == DispatchOutcome.PUBLISHED
        assert chg_disp.event_id == schedule_chg_envelope.event_id

        # Wait for notification.schedule_changed_consumer
        chg_inbox = await _wait_for_inbox_record(
            uow_factory=uow_factory,
            consumer_name="notification.schedule_changed_consumer",
            event_id=schedule_chg_envelope.event_id,
        )
        assert chg_inbox.event_id == schedule_chg_envelope.event_id
        assert chg_inbox.consumer_name == "notification.schedule_changed_consumer"
        receipts_before = dispatcher.get_receipts_by_correlation(root_correlation)
        assert any(r.trigger.source_event_name == "schedule.changed" for r in receipts_before)
        assert any(r.trigger.source_event_name == "schedule.published" for r in receipts_before)
        assert len(receipts_before) in (2, 3)

        # Replay duplicate schedule.changed through broker
        await publisher.publish(schedule_chg_envelope)
        await asyncio.sleep(0.5)
        receipts_after = dispatcher.get_receipts_by_correlation(root_correlation)
        assert len(receipts_after) == len(receipts_before)
        print("[Phase-6 Live E2E] schedule.changed broker delivery & dedupe verified.")

        # 14. Operations Dashboard Verification
        analytics_service = OperationsAnalyticsService()
        async with uow_factory() as uow:
            dashboard = await analytics_service.get_operations_summary(
                session=uow.session,
                tenant_id=tenant_id,
                goal_id=goal_resp.id,
            )
        assert dashboard.tenant_id == tenant_id
        assert dashboard.plan_progress is not None
        assert dashboard.blockers is not None
        assert dashboard.sla_state is not None
        assert dashboard.schedule_health is not None
        assert dashboard.recent_events is not None
        pct = dashboard.plan_progress.progress_percentage
        print(f"[Phase-6 Live E2E] Operations Dashboard: Plan Progress {pct}%")
        print(f"[Phase-6 Live E2E] Operations Dashboard: Blockers {len(dashboard.blockers)}")
        print(f"[Phase-6 Live E2E] Operations Dashboard: SLA State {dashboard.sla_state.status}")
        print(
            f"[Phase-6 Live E2E] Operations Dashboard: Schedule Health "
            f"{dashboard.schedule_health.status}"
        )
        print(
            f"[Phase-6 Live E2E] Operations Dashboard: Recent Events Count "
            f"{len(dashboard.recent_events)}"
        )
        for rev in dashboard.recent_events:
            print(
                f"  [Dashboard Recent Event] {rev.event_type} ({rev.outcome}) "
                f"corr={rev.correlation_id}"
            )

        # 15. Audit Correlation Completeness
        async with uow_factory() as uow:
            audit_stmt = select(AuditEventRecord).where(
                AuditEventRecord.tenant_id == tenant_id,
                AuditEventRecord.correlation_id == root_correlation,
            )
            audit_records = list((await uow.session.execute(audit_stmt)).scalars().all())
            assert len(audit_records) >= 15
            audit_types = {a.event_type for a in audit_records}
            print(
                f"[Phase-6 Live E2E] Persisted Correlated Audit Events Count: "
                f"{len(audit_records)}"
            )
            print(f"[Phase-6 Live E2E] Audit Event Types: {sorted(audit_types)}")

        # 16. Measured Outbox & Inbox Counts
        async with uow_factory() as uow:
            outbox_all_stmt = select(OutboxEventRecord).where(
                OutboxEventRecord.envelope["tenant_id"].astext == tenant_id
            )
            outbox_all = list((await uow.session.execute(outbox_all_stmt)).scalars().all())
            outbox_by_type: dict[str, int] = {}
            for o in outbox_all:
                evt_type = str(o.envelope.get("event_type", "unknown"))
                outbox_by_type[evt_type] = outbox_by_type.get(evt_type, 0) + 1

            inbox_all_stmt = select(InboxMessageRecord).where(
                InboxMessageRecord.recorded_at >= test_start_time
            )
            inbox_all = list((await uow.session.execute(inbox_all_stmt)).scalars().all())
            inbox_by_consumer: dict[str, int] = {}
            for i in inbox_all:
                inbox_by_consumer[i.consumer_name] = (
                    inbox_by_consumer.get(i.consumer_name, 0) + 1
                )

        print(f"[Phase-6 Live E2E] Measured Outbox Counts: {outbox_by_type}")
        print(f"[Phase-6 Live E2E] Measured Inbox Counts: {inbox_by_consumer}")

    finally:
        # Graceful shutdown of runners, publisher, flowable adapter, engine
        stop_event.set()
        await asyncio.gather(*runner_tasks, return_exceptions=True)
        await publisher.close()
        await flowable_adapter.close()
        await engine.dispose()
