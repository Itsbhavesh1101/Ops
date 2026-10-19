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
}

// Canonical default singleton configured to point to gateway/api
export const apiClient = new WorkflowApiClient(
  API_BASE_URL.endsWith("/api/v1") ? API_BASE_URL : `${API_BASE_URL}/api/v1`
);
