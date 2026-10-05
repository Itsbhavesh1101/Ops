import { describe, it, expect, vi, beforeEach } from "vitest";
import { WorkflowApiClient, ApiError } from "./client";

describe("WorkflowApiClient Contract & Route Alignment", () => {
  let client: WorkflowApiClient;
  const mockFetch = vi.fn();

  beforeEach(() => {
    mockFetch.mockReset();
    vi.stubGlobal("fetch", mockFetch);
    client = new WorkflowApiClient("/api/v1");
  });

  it("validateBpmn maps to supported /workflow/validate-bpmn endpoint", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ valid: true, process_id: "test_process" }),
    });

    const res = await client.validateBpmn("<bpmn/>");
    expect(res.valid).toBe(true);
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/workflow/validate-bpmn",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("registerDefinition maps to supported /workflow/definitions endpoint", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: "def-1", workflow_key: "wf1", version: 1 }),
    });

    const res = await client.registerDefinition("tenant-1", "wf1", "Workflow 1", "<bpmn/>");
    expect(res.id).toBe("def-1");
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/workflow/definitions",
      expect.objectContaining({
        method: "POST",
        headers: expect.any(Headers),
      })
    );
  });

  it("deployDefinition maps to supported /workflow/definitions/:id/deploy endpoint", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: "def-1", deployment_id: "dep-99" }),
    });

    const res = await client.deployDefinition("tenant-1", "def-123");
    expect(res.deployment_id).toBe("dep-99");
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/workflow/definitions/def-123/deploy",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("listTasks maps to supported /workflow/tasks endpoint", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [{ id: "t1", status: "PENDING" }],
    });

    const tasks = await client.listTasks("tenant-1");
    expect(tasks).toHaveLength(1);
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/workflow/tasks",
      expect.objectContaining({ method: "GET" })
    );
  });

  it("performTaskAction maps to supported /workflow/tasks/:id/action endpoint with Idempotency-Key", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: "COMPLETED", task_id: "task-456" }),
    });

    const res = await client.performTaskAction(
      "tenant-1",
      "task-456",
      "approve",
      { decision: "approved" },
      "idemp-key-777"
    );
    expect(res.status).toBe("COMPLETED");
    const [callUrl, callInit] = mockFetch.mock.calls[0];
    expect(callUrl).toBe("/api/v1/workflow/tasks/task-456/action");
    expect(callInit.headers.get("Idempotency-Key")).toBe("idemp-key-777");
    expect(callInit.headers.get("X-Tenant-ID")).toBe("tenant-1");
  });

  it("deployDecision maps to supported /workflow/dmn/deploy endpoint", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ decision_key: "dec_1", rule_count: 5 }),
    });

    const res = await client.deployDecision("tenant-1", "<dmn/>", "decision.dmn");
    expect(res.decision_key).toBe("dec_1");
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/workflow/dmn/deploy",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("executeDecision maps to supported /workflow/dmn/execute endpoint", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ decision_key: "dec_1", result_variables: { approved: true } }),
    });

    const res = await client.executeDecision("tenant-1", "dec_1", { amount: 100 });
    expect(res.decision_key).toBe("dec_1");
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/workflow/dmn/execute",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("raises ApiError with detail on non-200 responses", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 403,
      json: async () => ({ detail: "Actor ineligible for task" }),
    });

    await expect(
      client.performTaskAction("tenant-1", "task-1", "approve", {}, "key-1")
    ).rejects.toThrow(ApiError);
  });
});
