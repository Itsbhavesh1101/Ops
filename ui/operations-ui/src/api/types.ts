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

// --- GOALS DOMAIN (F2) ---
export interface TargetWindow {
  start_at?: string | null;
  end_at?: string | null;
}

export interface GoalCreate {
  objective: string;
  scope?: string | null;
  deadline?: string | null;
  target_window?: TargetWindow | null;
  constraints?: string[];
  resource_scope?: string[];
  context_references?: string[];
  priority_target?: string | null;
  metadata?: Record<string, unknown>;
}

export interface GoalResponse {
  id: string;
  tenant_id: string;
  objective: string;
  scope?: string | null;
  deadline?: string | null;
  target_window?: TargetWindow | null;
  constraints: string[];
  resource_scope: string[];
  context_references: string[];
  priority_target?: string | null;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// --- PLANS DOMAIN (F2) ---
export interface PlanResponse {
  id: string;
  goal_id: string;
  version_number: number;
  plan_name: string;
  status: string;
  is_baseline: boolean;
  baseline_hash?: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlanApprovalState {
  status: string;
  is_baseline: boolean;
  baseline_hash?: string | null;
}

export interface TaskDetailResponse {
  id: string;
  plan_id: string;
  temporary_id: string;
  title: string;
  description?: string | null;
  wbs_code?: string | null;
  owner_role?: string | null;
  owner_reference?: string | null;
  estimated_duration_minutes: number;
  actual_duration_minutes?: number | null;
  status: string;
  progress_percentage: number;
  blocked_reason?: string | null;
  priority: string;
  work_category?: string | null;
  acceptance_criteria?: string[];
  parent_task_id?: string | null;
  milestone_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DependencyDetailResponse {
  id: string;
  predecessor_id: string;
  successor_id: string;
  dependency_type: string;
  lag_minutes: number;
}

export interface MilestoneDetailResponse {
  id: string;
  temporary_id: string;
  name: string;
  description?: string | null;
  target_date?: string | null;
}

export interface RiskDetailResponse {
  id: string;
  task_id: string;
  category: string;
  severity: string;
  description: string;
  is_evaluable: boolean;
  detected_at: string;
}

export interface PlanDetailResponse {
  id: string;
  goal_id: string;
  version_number: number;
  plan_name: string;
  status: string;
  is_baseline: boolean;
  superseded_by_plan_id?: string | null;
  baseline_hash?: string | null;
  approval_state?: PlanApprovalState | null;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  tasks?: TaskDetailResponse[];
  dependencies?: DependencyDetailResponse[];
  milestones?: MilestoneDetailResponse[];
  risks?: RiskDetailResponse[];
}

export interface ValidationIssue {
  code: string;
  location: string;
  message: string;
  severity?: "INFO" | "WARNING" | "ERROR" | "CRITICAL";
  repairable?: boolean;
  context?: Record<string, unknown>;
}

export interface ValidationResult {
  is_valid: boolean;
  issues?: ValidationIssue[];
  evaluated_task_count?: number;
  evaluated_dependency_count?: number;
  cycle_path?: string[] | null;
  topological_order?: string[] | null;
  critical_path?: string[] | null;
}

export interface CPMScheduleNode {
  task_id: string;
  duration_minutes: number;
  early_start: number;
  early_finish: number;
  late_start: number;
  late_finish: number;
  total_slack: number;
  free_slack?: number;
  is_critical: boolean;
}

export interface CPMScheduleResult {
  project_duration_minutes: number;
  schedule: Record<string, CPMScheduleNode>;
  critical_path: string[];
  critical_paths?: string[][];
}

// --- TASKS PROGRESS DOMAIN (F2) ---
export type TaskProgressStatus = "TODO" | "IN_PROGRESS" | "BLOCKED" | "COMPLETED";

export interface TaskProgressUpdate {
  status?: TaskProgressStatus | null;
  progress_percentage?: number | null;
  actual_duration_minutes?: number | null;
  blocked_reason?: string | null;
}

export interface TaskResponse {
  id: string;
  plan_id: string;
  temporary_id: string;
  title: string;
  status: string;
  progress_percentage: number;
  actual_duration_minutes?: number | null;
  blocked_reason?: string | null;
  updated_at: string;
}

// --- RESOURCES DOMAIN (F2) ---
export type ResourceType = "PERSON" | "ROOM" | "EQUIPMENT" | "OTHER";

export interface ResourceCatalogItem {
  id: string;
  resource_key: string;
  type: ResourceType | string;
  display_label: string;
  capacity: number;
  skills?: string[];
  attributes?: Record<string, unknown>;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ResourceAvailabilityItem {
  resource_id: string;
  interval_type: string;
  start_at: string;
  end_at: string;
  reason?: string | null;
}

export interface ResourceCreateRequest {
  resource_key: string;
  type: ResourceType | string;
  display_label: string;
  capacity?: number;
  skills?: string[];
  attributes?: Record<string, unknown>;
  active?: boolean;
}

export interface ResourceAvailabilityCreateRequest {
  resource_id: string;
  interval_type: "AVAILABLE" | "UNAVAILABLE";
  start_at: string;
  end_at: string;
  reason?: string;
}

// --- SCHEDULING DOMAIN (F2) ---
export interface TimeWindow {
  start_at: string;
  end_at: string;
}

export interface SchedulingActivity {
  id: string;
  title: string;
  duration_minutes: number;
  priority?: string;
  allowed_windows?: TimeWindow[];
  required_skills?: string[];
  required_resource_ids?: string[];
  predecessors?: string[];
  preferred_resource_ids?: string[];
  locked_assignment?: unknown | null;
  baseline_assignment?: unknown | null;
}

export interface SchedulingResourceSnapshot {
  id: string;
  type: ResourceType | string;
  display_label: string;
  capacity?: number;
  skills?: string[];
  unavailability_windows?: TimeWindow[];
}

export interface SoftObjectiveWeights {
  preference_weight?: number;
  idle_gap_weight?: number;
  fairness_weight?: number;
  utilization_weight?: number;
  movement_weight?: number;
}

export interface SolverProfile {
  profile_key: string;
  version?: number;
  name: string;
  description?: string;
  horizon_start: string;
  horizon_end: string;
  time_granularity_minutes: number;
  max_solve_seconds: number;
  random_seed?: number | null;
  num_search_workers: number;
  enabled_hard_constraints?: string[];
  soft_weights: SoftObjectiveWeights;
}

export interface SchedulingProblemDefinition {
  schedule_key: string;
  horizon_start: string;
  horizon_end: string;
  timezone?: string;
  activities: SchedulingActivity[];
  resources: SchedulingResourceSnapshot[];
  constraints?: Array<Record<string, unknown>>;
  profile: SolverProfile;
}

export interface ScheduleJobSubmitRequest {
  schedule_key?: string;
  request: SchedulingProblemDefinition;
}

export interface ScheduleJobResponse {
  job_id: string;
  schedule_key: string;
  status: "QUEUED" | "CLAIMED" | "SOLVING" | "FEASIBLE" | "INFEASIBLE" | "FAILED" | string;
  input_snapshot_hash: string;
  solver_metadata: Record<string, unknown>;
  diagnostics: Array<Record<string, unknown> | string>;
  failure_message?: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface SchedulePublishRequest {
  principal?: string;
}

export interface SchedulePublishResponse {
  schedule_id: string;
  version: number;
  status: string;
  published_at: string;
  published_by: string;
  supersedes_id?: string | null;
  assignment_count: number;
}

export interface ScheduleListItem {
  id: string;
  schedule_key: string;
  version: number;
  status: string;
  source_job_id: string;
  input_snapshot_hash: string;
  solver_profile_version?: number | null;
  supersedes_id?: string | null;
  published_at?: string | null;
  published_by?: string | null;
  repair_reason?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduleAssignmentItem {
  id: string;
  schedule_id: string;
  activity_id: string;
  resource_id: string;
  start_at: string;
  end_at: string;
  is_locked: boolean;
  metadata_json?: Record<string, unknown>;
  created_at: string;
}

export interface ScheduleDetailResponse {
  id: string;
  schedule_key: string;
  version: number;
  status: string;
  source_job_id: string;
  input_snapshot_hash: string;
  solver_profile_version?: number | null;
  solver_metadata?: Record<string, unknown>;
  score_components?: Record<string, unknown>;
  supersedes_id?: string | null;
  published_at?: string | null;
  published_by?: string | null;
  repair_reason?: string | null;
  repair_scope?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  assignments?: ScheduleAssignmentItem[];
}

export interface ScheduleRepairRequest {
  disrupted_resource_ids?: string[];
  disrupted_interval_start?: string | null;
  disrupted_interval_end?: string | null;
  affected_activity_ids?: string[];
  reason?: string | null;
  movement_weight?: number | null;
  profile?: SolverProfile | null;
}

export interface ScheduleRepairResponse {
  job_id: string;
  baseline_schedule_id: string;
  candidate_schedule_id?: string | null;
  candidate_version?: number | null;
  status: string;
  diagnostics?: Array<Record<string, unknown> | string>;
}
