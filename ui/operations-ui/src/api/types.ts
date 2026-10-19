/**
 * Shared Type Definitions for Operations Engine Frontend
 */

export interface AuthenticatedUser {
  principal_id: string;
  tenant_id: string;
  roles: string[];
  permissions: string[];
  principal_type: "HUMAN" | "SERVICE";
  authentication_method: "OIDC" | "SERVICE_API_KEY";
  display_name?: string | null;
  email?: string | null;
  credential_id?: string | null;
}

export interface HealthResponse {
  status: "ok" | "degraded" | "error";
  service: string;
  environment: string;
  timestamp?: string;
  details?: Record<string, unknown>;
}

export interface ReadyResponse {
  status: "ready" | "not_ready";
  service: string;
  environment: string;
  checks?: Record<string, boolean>;
}

export interface ApiErrorDetail {
  code?: string;
  message: string;
  details?: unknown;
}

export interface PlanProgressSummary {
  plan_id?: string | null;
  plan_name?: string | null;
  total_tasks: number;
  completed_tasks: number;
  progress_percentage: number;
  current_milestone?: string | null;
  status?: string | null;
}

export interface BlockerItem {
  task_id: string;
  temporary_id: string;
  title: string;
  blocked_reason?: string | null;
}

export interface SlaStateSummary {
  status: string;
  active_workflows_count: number;
  warning_count: number;
  breached_count: number;
}

export interface ScheduleHealthSummary {
  schedule_id?: string | null;
  version: number;
  status?: string | null;
  hard_violations: number;
  solver_status?: string | null;
  calendar_sync_status: string;
}

export interface RepairMovementSummary {
  latest_repair_id?: string | null;
  moved_assignments_count: number;
  locked_assignments_moved: number;
  repair_reason?: string | null;
}

export interface RecentEventItem {
  event_type: string;
  resource_type: string;
  resource_id: string;
  outcome?: string | null;
  recorded_at: string;
  correlation_id?: string | null;
}

export interface RepairStabilitySummary {
  total_repairs_count: number;
  average_percentage_moved: number;
  locked_moved_count: number;
}

export interface RiskSummary {
  open_risks_count: number;
  risks_by_severity: Record<string, number>;
  stale_resources_count: number;
  repair_queue_depth: number;
  repair_stability: RepairStabilitySummary;
}

export interface OperationsDashboardSummary {
  tenant_id: string;
  plan_progress: PlanProgressSummary;
  blockers: BlockerItem[];
  sla_state: SlaStateSummary;
  schedule_health: ScheduleHealthSummary;
  repair_movement: RepairMovementSummary;
  recent_events: RecentEventItem[];
  risk_summary?: RiskSummary;
}
