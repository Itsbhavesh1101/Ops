# Phase 3 Advanced Approval Models

## 1. Typed Approval Configurations
The approval subsystem (`app/workflow/approval/`) provides typed models and an E-7 controlled compiler (`ApprovalCompiler`) that maps high-level approval patterns directly into executable Flowable BPMN:
- **SINGLE**: One designated approver or role pool.
- **SEQUENTIAL**: Ordered chain of approval steps. Each step must complete before the next activates.
- **PARALLEL_ALL_OF**: Forks into concurrent user tasks. All approvers must approve before advancing.
- **QUORUM_N_OF_M**: Multi-instance user task requiring $N$ of $M$ approvals ($N > 0$, $M > 0$, $N \le M$). Advance occurs as soon as $N$ approvals are recorded. Replay or excess votes are ignored idempotently.
- **ROLE_POOL**: Dynamic routing across role pools (`candidateGroups`) filtered by tenant and principal roles.

## 2. Deterministic Rejection Semantics
Rejection policies are explicitly defined in configuration:
- `ANY_REJECTION_FAILS`: A single rejection immediately terminates or routes the workflow to rejection handling.
- `QUORUM_REJECTION`: Workflow transitions to rejected only if $(M - N + 1)$ rejections occur, making quorum mathematically impossible.

## 3. Delegation Contract
- `DelegationPolicy`: Formal contract supporting explicit, time-bounded, and scope-restricted delegation grants.
- Cross-tenant delegation is strictly prohibited.
- Production delegation authority source remains decoupled:
  `SLA_BUSINESS_CALENDAR_PRODUCTION_SOURCE = DEFERRED_PENDING_TENANT_POLICY_CONFIGURATION`.
