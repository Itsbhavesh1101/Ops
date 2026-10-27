/**
 * E-7 Operations Engine - Production Frontend API Client Layer
 *
 * Implements typed, decoupled communication between Operations UI and backend APIs.
 * Supports:
 * - Environment-driven base URL configuration (VITE_API_BASE_URL)
 * - Session token management (Bearer token or Service API Key)
 * - Canonical endpoints: /auth/me, /health, /ready, /analytics/operations, and workflows
 * - Deterministic error normalization (ApiError)
 */

import { API_BASE_URL } from "./config";
import type {
  AuthenticatedUser,
  HealthResponse,
  ReadyResponse,
  OperationsDashboardSummary,
  PlanProgressSummary,
  BlockerItem,
  SlaStateSummary,
  ScheduleHealthSummary,
  RepairMovementSummary,
  RecentEventItem,
  RiskSummary,
  GoalCreate,
  GoalResponse,
  PlanResponse,
  PlanDetailResponse,
  TaskDetailResponse,
  DependencyDetailResponse,
  MilestoneDetailResponse,
  RiskDetailResponse,
  ValidationResult,
  ValidationIssue,
  CPMScheduleResult,
  CPMScheduleNode,
  TaskProgressStatus,
  TaskProgressUpdate,
  TaskResponse,
  ResourceType,
  ResourceCatalogItem,
  ResourceAvailabilityItem,
  ResourceCreateRequest,
  ResourceAvailabilityCreateRequest,
  ScheduleJobSubmitRequest,
  ScheduleJobResponse,
  SchedulePublishRequest,
  SchedulePublishResponse,
  ScheduleListItem,
  ScheduleDetailResponse,
  ScheduleAssignmentItem,
  ScheduleRepairRequest,
  ScheduleRepairResponse,
  SchedulingProblemDefinition,
  SchedulingActivity,
  SchedulingResourceSnapshot,
  SolverProfile,
  WorkflowInstanceStatus,
  WorkflowInstanceResponse,
  WorkflowHistoryEntry,
  AuditEventResponse,
  AuditQueryParams,
  MilestoneHealth,
  MilestoneHealthResponse,
  TaskRiskResponse,
  RecommendationQuality,
  RecommendationAction,
  TaskRecommendation,
  PlanRecommendationsResponse,
  RecommendationQueryParams,
} from "./types";

export type {
  AuthenticatedUser,
  HealthResponse,
  ReadyResponse,
  OperationsDashboardSummary,
  PlanProgressSummary,
  BlockerItem,
  SlaStateSummary,
  ScheduleHealthSummary,
  RepairMovementSummary,
  RecentEventItem,
  RiskSummary,
  GoalCreate,
  GoalResponse,
  PlanResponse,
  PlanDetailResponse,
  TaskDetailResponse,
  DependencyDetailResponse,
  MilestoneDetailResponse,
  RiskDetailResponse,
  ValidationResult,
  ValidationIssue,
  CPMScheduleResult,
  CPMScheduleNode,
  TaskProgressStatus,
  TaskProgressUpdate,
  TaskResponse,
  ResourceType,
  ResourceCatalogItem,
  ResourceAvailabilityItem,
  ResourceCreateRequest,
  ResourceAvailabilityCreateRequest,
  ScheduleJobSubmitRequest,
  ScheduleJobResponse,
  SchedulePublishRequest,
  SchedulePublishResponse,
  ScheduleListItem,
  ScheduleDetailResponse,
  ScheduleAssignmentItem,
  ScheduleRepairRequest,
  ScheduleRepairResponse,
  SchedulingProblemDefinition,
  SchedulingActivity,
  SchedulingResourceSnapshot,
  SolverProfile,
  WorkflowInstanceStatus,
  WorkflowInstanceResponse,
  WorkflowHistoryEntry,
  AuditEventResponse,
  AuditQueryParams,
  MilestoneHealth,
  MilestoneHealthResponse,
  TaskRiskResponse,
  RecommendationQuality,
  RecommendationAction,
  TaskRecommendation,
  PlanRecommendationsResponse,
  RecommendationQueryParams,
};

export interface WorkflowDefinition {
  id: string;
  tenant_id: string;
  workflow_key: string;
  name: string;
  version: number;
  bpmn_process_id: string;
  artifact_hash: string;
  deployment_id: string | null;
  is_active: boolean;
}

export interface WorkflowTask {
  id: string;
  tenant_id: string;
  workflow_instance_id?: string;
  task_definition_key: string;
  name: string;
  description?: string | null;
  status: "PENDING" | "CLAIMED" | "COMPLETED";
  assignee?: string | null;
  candidate_roles: string[];
  form_key?: string | null;
  sla_status?: "RUNNING" | "WARNING" | "BREACHED" | "PAUSED" | "COMPLETED" | null;
  warning_at?: string | null;
  due_at?: string | null;
  created_at: string;
}

export interface FormComponent {
  id: string;
  key: string;
  label: string;
  type: "textfield" | "number" | "checkbox" | "select" | "datetime";
  required?: boolean;
  options?: Array<{ label: string; value: string }>;
}

export interface FormDefinition {
  schema_version: string;
  title: string;
  components: FormComponent[];
}

export interface DmnDecisionMetadata {
  decision_key: string;
  name: string;
  hit_policy: "UNIQUE" | "FIRST" | "RULE_ORDER" | "COLLECT";
  rule_count: number;
  artifact_hash: string;
}

export interface DmnEvaluationResponse {
  decision_key: string;
  result_variables: Record<string, unknown>;
  evaluated_at: string;
}

export class ApiError extends Error {
  public code?: string;
  constructor(
    public status: number,
    message: string,
    public details?: unknown,
    code?: string
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

export class WorkflowApiClient {
  private authToken: string | null = null;
  private activeTenantId: string | null = null;

  constructor(private baseUrl: string = "/api/v1") {}

  public setAuthToken(token: string | null): void {
    if (token && token.trim().startsWith("e7sk_")) {
      throw new Error(
        "Security Violation: Service API keys (e7sk_...) are machine-to-machine credentials and must not be used for browser authentication."
      );
    }
    this.authToken = token ? token.trim() : null;
  }

  public getAuthToken(): string | null {
    return this.authToken;
  }

  public setActiveTenantId(tenantId: string | null): void {
    this.activeTenantId = tenantId;
  }

  public getActiveTenantId(): string | null {
    return this.activeTenantId;
  }

  public async request<T>(
    path: string,
    options: RequestInit = {}
  ): Promise<T> {
    const headers = new Headers(options.headers || {});
    if (!headers.has("Content-Type") && options.body && typeof options.body === "string") {
      headers.set("Content-Type", "application/json");
    }

    // Attach human Bearer token if present (never service key from browser state)
    if (this.authToken && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${this.authToken}`);
    }

    // Attach active tenant ID if present and not already specified
    if (this.activeTenantId && !headers.has("X-Tenant-ID")) {
      headers.set("X-Tenant-ID", this.activeTenantId);
    }

    // Determine target URL
    const targetUrl = path.startsWith("http")
      ? path
      : `${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`;

    let response: Response;
    try {
      response = await fetch(targetUrl, {
        ...options,
        headers,
      });
    } catch (networkErr: unknown) {
      const msg = networkErr instanceof Error ? networkErr.message : "Network request failed";
      throw new ApiError(0, `Network error: ${msg}`, networkErr);
    }

    if (!response.ok) {
      let errDetails: unknown = null;
      let errMsg = `Request failed with status ${response.status}`;
      let errCode: string | undefined = undefined;

      try {
        const data = await response.json();
        errDetails = data;
        if (data.code) {
          errCode = String(data.code);
        }
        if (data.message) {
          errMsg = String(data.message);
        } else if (data.detail) {
          errMsg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
        }
      } catch {
        // Fall back to HTTP status text
        if (response.statusText) {
          errMsg = `${response.status} ${response.statusText}`;
        }
      }

      throw new ApiError(response.status, errMsg, errDetails, errCode);
    }

    // Handle 204 No Content
    if (response.status === 204) {
      return {} as T;
    }

    return response.json();
  }

  // --- Auth & Identity Domain ---
  async getCurrentUser(): Promise<AuthenticatedUser> {
    return this.request<AuthenticatedUser>("/auth/me", { method: "GET" });
  }

  // --- System Health Domain ---
  async getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/health", { method: "GET" });
  }

  async getReady(): Promise<ReadyResponse> {
    return this.request<ReadyResponse>("/ready", { method: "GET" });
  }

  // --- Operations Analytics Domain ---
  async getOperationsDashboard(
    tenantId: string,
    goalId?: string
  ): Promise<OperationsDashboardSummary> {
    const query = goalId ? `?goal_id=${encodeURIComponent(goalId)}` : "";
    return this.request<OperationsDashboardSummary>(`/analytics/operations${query}`, {
      method: "GET",
      headers: {
        "X-Tenant-ID": tenantId,
      },
    });
  }

  // --- Workflow Subsystem Methods ---
  async validateBpmn(bpmnXml: string): Promise<{ valid: boolean; process_id?: string; error?: string }> {
    return this.request<{ valid: boolean; process_id?: string; error?: string }>(
      "/workflow/validate-bpmn",
      {
        method: "POST",
        body: JSON.stringify({ bpmn_xml: bpmnXml }),
      }
    );
  }

  async registerDefinition(
    tenantId: string,
    workflowKey: string,
    name: string,
    bpmnXml: string
  ): Promise<WorkflowDefinition> {
    return this.request<WorkflowDefinition>("/workflow/definitions", {
      method: "POST",
      headers: { "X-Tenant-ID": tenantId },
      body: JSON.stringify({
        workflow_key: workflowKey,
        name,
        bpmn_xml: bpmnXml,
      }),
    });
  }

  async deployDefinition(
    tenantId: string,
    definitionId: string
  ): Promise<WorkflowDefinition> {
    return this.request<WorkflowDefinition>(
      `/workflow/definitions/${definitionId}/deploy`,
      {
        method: "POST",
        headers: { "X-Tenant-ID": tenantId },
      }
    );
  }

  async listTasks(tenantId: string): Promise<WorkflowTask[]> {
    return this.request<WorkflowTask[]>("/workflow/tasks", {
      method: "GET",
      headers: { "X-Tenant-ID": tenantId },
    });
  }

  async performTaskAction(
    tenantId: string,
    taskId: string,
    action: "claim" | "unclaim" | "complete" | "approve" | "reject",
    variables: Record<string, unknown>,
    idempotencyKey: string
  ): Promise<{ status: string; task_id: string }> {
    return this.request<{ status: string; task_id: string }>(
      `/workflow/tasks/${taskId}/action`,
      {
        method: "POST",
        headers: {
          "X-Tenant-ID": tenantId,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({
          action,
          variables,
        }),
      }
    );
  }

  async deployDecision(
    tenantId: string,
    dmnXml: string,
    resourceName: string
  ): Promise<DmnDecisionMetadata> {
    return this.request<DmnDecisionMetadata>("/workflow/dmn/deploy", {
      method: "POST",
      headers: { "X-Tenant-ID": tenantId },
      body: JSON.stringify({
        dmn_xml: dmnXml,
        resource_name: resourceName,
      }),
    });
  }

  async executeDecision(
    tenantId: string,
    decisionKey: string,
    variables: Record<string, unknown>
  ): Promise<DmnEvaluationResponse> {
    return this.request<DmnEvaluationResponse>("/workflow/dmn/execute", {
      method: "POST",
      headers: { "X-Tenant-ID": tenantId },
      body: JSON.stringify({
        decision_key: decisionKey,
        variables,
      }),
    });
  }

  // ==========================================
  // --- GOALS DOMAIN (Phase F2) ---
  // ==========================================
  async createGoal(payload: GoalCreate, tenantId?: string): Promise<GoalResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<GoalResponse>("/goals", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
  }

  async listGoals(
    params?: { limit?: number; offset?: number },
    tenantId?: string
  ): Promise<GoalResponse[]> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    const q = new URLSearchParams();
    if (params?.limit !== undefined) q.set("limit", String(params.limit));
    if (params?.offset !== undefined) q.set("offset", String(params.offset));
    const query = q.toString() ? `?${q.toString()}` : "";
    return this.request<GoalResponse[]>(`/goals${query}`, {
      method: "GET",
      headers,
    });
  }

  async getGoal(goalId: string, tenantId?: string): Promise<GoalResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<GoalResponse>(`/goals/${encodeURIComponent(goalId)}`, {
      method: "GET",
      headers,
    });
  }

  // ==========================================
  // --- PLANS & WBS DOMAIN (Phase F2) ---
  // ==========================================
  async generatePlan(
    goalId: string,
    proposal?: Record<string, unknown> | null,
    tenantId?: string
  ): Promise<PlanResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<PlanResponse>(`/goals/${encodeURIComponent(goalId)}/generate-plan`, {
      method: "POST",
      headers,
      body: proposal ? JSON.stringify(proposal) : JSON.stringify({}),
    });
  }

  async listPlans(
    params?: { goal_id?: string; limit?: number; offset?: number },
    tenantId?: string
  ): Promise<PlanResponse[]> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    const q = new URLSearchParams();
    if (params?.goal_id) q.set("goal_id", params.goal_id);
    if (params?.limit !== undefined) q.set("limit", String(params.limit));
    if (params?.offset !== undefined) q.set("offset", String(params.offset));
    const query = q.toString() ? `?${q.toString()}` : "";
    return this.request<PlanResponse[]>(`/plans${query}`, {
      method: "GET",
      headers,
    });
  }

  async getPlan(planId: string, tenantId?: string): Promise<PlanDetailResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<PlanDetailResponse>(`/plans/${encodeURIComponent(planId)}`, {
      method: "GET",
      headers,
    });
  }

  async getPlanTasks(planId: string, tenantId?: string): Promise<TaskDetailResponse[]> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<TaskDetailResponse[]>(`/plans/${encodeURIComponent(planId)}/tasks`, {
      method: "GET",
      headers,
    });
  }

  async validatePlan(planId: string, tenantId?: string): Promise<ValidationResult> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<ValidationResult>(`/plans/${encodeURIComponent(planId)}/validate`, {
      method: "POST",
      headers,
    });
  }

  async submitPlanForApproval(planId: string, tenantId?: string): Promise<PlanResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<PlanResponse>(`/plans/${encodeURIComponent(planId)}/submit-for-approval`, {
      method: "POST",
      headers,
    });
  }

  async approvePlan(planId: string, tenantId?: string): Promise<PlanResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<PlanResponse>(`/plans/${encodeURIComponent(planId)}/approve`, {
      method: "POST",
      headers,
    });
  }

  async getCriticalPath(planId: string, tenantId?: string): Promise<CPMScheduleResult> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<CPMScheduleResult>(`/plans/${encodeURIComponent(planId)}/critical-path`, {
      method: "GET",
      headers,
    });
  }

  // ==========================================
  // --- TASKS PROGRESS DOMAIN (Phase F2) ---
  // ==========================================
  async updateTaskProgress(
    taskId: string,
    payload: TaskProgressUpdate,
    tenantId?: string
  ): Promise<TaskResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<TaskResponse>(`/tasks/${encodeURIComponent(taskId)}`, {
      method: "PATCH",
      headers,
      body: JSON.stringify(payload),
    });
  }

  // ==========================================
  // --- RESOURCES DOMAIN (Phase F2) ---
  // ==========================================
  async listResources(
    params?: { type?: string; active_only?: boolean; limit?: number; offset?: number },
    tenantId?: string
  ): Promise<ResourceCatalogItem[]> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    const q = new URLSearchParams();
    if (params?.type) q.set("type", params.type);
    if (params?.active_only !== undefined) q.set("active_only", String(params.active_only));
    if (params?.limit !== undefined) q.set("limit", String(params.limit));
    if (params?.offset !== undefined) q.set("offset", String(params.offset));
    const query = q.toString() ? `?${q.toString()}` : "";
    return this.request<ResourceCatalogItem[]>(`/resources${query}`, {
      method: "GET",
      headers,
    });
  }

  async listAvailability(
    params?: { resource_id?: string },
    tenantId?: string
  ): Promise<ResourceAvailabilityItem[]> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    const q = new URLSearchParams();
    if (params?.resource_id) q.set("resource_id", params.resource_id);
    const query = q.toString() ? `?${q.toString()}` : "";
    return this.request<ResourceAvailabilityItem[]>(`/resources/availability${query}`, {
      method: "GET",
      headers,
    });
  }

  async createResource(
    payload: ResourceCreateRequest,
    tenantId?: string
  ): Promise<ResourceCatalogItem> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<ResourceCatalogItem>("/resources", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
  }

  async createAvailability(
    payload: ResourceAvailabilityCreateRequest,
    tenantId?: string
  ): Promise<ResourceAvailabilityItem> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<ResourceAvailabilityItem>("/resources/availability", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
  }

  // ==========================================
  // --- SCHEDULING JOBS & TIMETABLE (Phase F2) ---
  // ==========================================
  async createScheduleJob(
    payload: ScheduleJobSubmitRequest,
    tenantId?: string
  ): Promise<ScheduleJobResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<ScheduleJobResponse>("/schedule-jobs", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
  }

  async getScheduleJob(jobId: string, tenantId?: string): Promise<ScheduleJobResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<ScheduleJobResponse>(`/schedule-jobs/${encodeURIComponent(jobId)}`, {
      method: "GET",
      headers,
    });
  }

  async listSchedules(
    params?: { status?: string; limit?: number; offset?: number },
    tenantId?: string
  ): Promise<ScheduleListItem[]> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    const q = new URLSearchParams();
    if (params?.status) q.set("status", params.status);
    if (params?.limit !== undefined) q.set("limit", String(params.limit));
    if (params?.offset !== undefined) q.set("offset", String(params.offset));
    const query = q.toString() ? `?${q.toString()}` : "";
    return this.request<ScheduleListItem[]>(`/schedules${query}`, {
      method: "GET",
      headers,
    });
  }

  async getSchedule(scheduleId: string, tenantId?: string): Promise<ScheduleDetailResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<ScheduleDetailResponse>(`/schedules/${encodeURIComponent(scheduleId)}`, {
      method: "GET",
      headers,
    });
  }

  async publishScheduleJob(
    jobId: string,
    payload?: SchedulePublishRequest,
    tenantId?: string
  ): Promise<SchedulePublishResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<SchedulePublishResponse>(`/schedule-jobs/${encodeURIComponent(jobId)}/publish`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload || {}),
    });
  }

  async repairSchedule(
    scheduleId: string,
    payload: ScheduleRepairRequest,
    tenantId?: string
  ): Promise<ScheduleRepairResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<ScheduleRepairResponse>(`/schedules/${encodeURIComponent(scheduleId)}/repair`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
  }

  // ==========================================
  // --- WORKFLOW INSTANCES & HISTORY (DQ-3) ---
  // ==========================================
  async getWorkflowInstance(
    instanceId: string,
    tenantId?: string
  ): Promise<WorkflowInstanceResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<WorkflowInstanceResponse>(
      `/workflows/instances/${encodeURIComponent(instanceId)}`,
      { method: "GET", headers }
    );
  }

  async getWorkflowHistory(
    instanceId: string,
    tenantId?: string
  ): Promise<WorkflowHistoryEntry[]> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<WorkflowHistoryEntry[]>(
      `/workflows/instances/${encodeURIComponent(instanceId)}/history`,
      { method: "GET", headers }
    );
  }

  async listWorkflowTasks(
    tenantId?: string,
    params?: { instance_id?: string; status?: string; assignee?: string }
  ): Promise<WorkflowTask[]> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    const q = new URLSearchParams();
    if (params?.instance_id) q.set("instance_id", params.instance_id);
    if (params?.status) q.set("status", params.status);
    if (params?.assignee) q.set("assignee", params.assignee);
    const query = q.toString() ? `?${q.toString()}` : "";
    return this.request<WorkflowTask[]>(`/workflows/tasks${query}`, {
      method: "GET",
      headers,
    });
  }

  // ==========================================
  // --- ENTERPRISE AUDIT TRAIL (DQ-3) ---
  // ==========================================
  async searchAudit(
    params?: AuditQueryParams,
    tenantId?: string
  ): Promise<AuditEventResponse[]> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    const q = new URLSearchParams();
    if (params?.correlation_id) q.set("correlation_id", params.correlation_id);
    if (params?.event_type) q.set("event_type", params.event_type);
    if (params?.resource_type) q.set("resource_type", params.resource_type);
    if (params?.resource_id) q.set("resource_id", params.resource_id);
    if (params?.limit !== undefined) q.set("limit", String(params.limit));
    if (params?.offset !== undefined) q.set("offset", String(params.offset));
    const query = q.toString() ? `?${q.toString()}` : "";
    return this.request<AuditEventResponse[]>(`/audit${query}`, {
      method: "GET",
      headers,
    });
  }

  // ==========================================
  // --- TASK INTELLIGENCE: MILESTONES, RISKS, RECOMMENDATIONS (DQ-2 / DQ-3) ---
  // ==========================================
  async getPlanMilestones(
    planId: string,
    tenantId?: string
  ): Promise<MilestoneHealthResponse[]> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<MilestoneHealthResponse[]>(
      `/plans/${encodeURIComponent(planId)}/milestones`,
      { method: "GET", headers }
    );
  }

  async getPlanRisks(
    planId: string,
    tenantId?: string
  ): Promise<TaskRiskResponse[]> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    return this.request<TaskRiskResponse[]>(
      `/plans/${encodeURIComponent(planId)}/risks`,
      { method: "GET", headers }
    );
  }

  async getPlanRecommendations(
    planId: string,
    params?: RecommendationQueryParams,
    tenantId?: string
  ): Promise<PlanRecommendationsResponse> {
    const headers: Record<string, string> = {};
    if (tenantId) headers["X-Tenant-ID"] = tenantId;
    const q = new URLSearchParams();
    if (params?.limit !== undefined) q.set("limit", String(params.limit));
    if (params?.include_blocked !== undefined) q.set("include_blocked", String(params.include_blocked));
    if (params?.critical_path_weight !== undefined) {
      q.set("critical_path_weight", String(params.critical_path_weight));
    }
    if (params?.priority_weight !== undefined) {
      q.set("priority_weight", String(params.priority_weight));
    }
    if (params?.urgency_weight !== undefined) {
      q.set("urgency_weight", String(params.urgency_weight));
    }
    if (params?.momentum_weight !== undefined) {
      q.set("momentum_weight", String(params.momentum_weight));
    }
    if (params?.readiness_weight !== undefined) {
      q.set("readiness_weight", String(params.readiness_weight));
    }
    const query = q.toString() ? `?${q.toString()}` : "";
    return this.request<PlanRecommendationsResponse>(
      `/plans/${encodeURIComponent(planId)}/recommendations${query}`,
      { method: "GET", headers }
    );
  }
}

// Canonical default singleton configured to point to gateway/api
export const apiClient = new WorkflowApiClient(
  API_BASE_URL.endsWith("/api/v1") ? API_BASE_URL : `${API_BASE_URL}/api/v1`
);
