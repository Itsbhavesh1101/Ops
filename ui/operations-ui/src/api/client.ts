/**
 * E-7 Operations Engine - Typed Workflow API Client (Phase 3B)
 *
 * Implements decoupled communication between Operations UI and backend APIs.
 * Invariants:
 * - Never manufactures tenant authority; tenantId is provided by caller context.
 * - Enforces client-generated Idempotency-Key on all task mutation actions.
 * - Handles server-side validation error presentation deterministically.
 */

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
  constructor(public status: number, message: string, public details?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

export class WorkflowApiClient {
  constructor(private baseUrl: string = "/api/v1") {}

  private async request<T>(
    path: string,
    options: RequestInit = {}
  ): Promise<T> {
    const headers = new Headers(options.headers || {});
    if (!headers.has("Content-Type") && options.body && typeof options.body === "string") {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers,
    });

    if (!response.ok) {
      let errDetails: unknown = null;
      let errMsg = `Request failed with status ${response.status}`;
      try {
        const data = await response.json();
        errDetails = data;
        if (data.detail) {
          errMsg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
        } else if (data.message) {
          errMsg = data.message;
        }
      } catch {
        // Fall back to status text
      }
      throw new ApiError(response.status, errMsg, errDetails);
    }

    return response.json();
  }

  async validateBpmn(bpmnXml: string): Promise<{ valid: boolean; process_id?: string; error?: string }> {
    // Client invokes server-side BpmnValidator
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
