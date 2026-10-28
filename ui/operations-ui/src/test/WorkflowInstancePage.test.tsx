import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { WorkflowInstancePage } from "../pages/WorkflowInstancePage";
import { AuthContextType } from "../context/AuthContext";
import { apiClient, ApiError } from "../api/client";
import type { WorkflowInstanceResponse, WorkflowHistoryEntry, WorkflowTask } from "../api/types";
import { OIDC_CONFIG } from "../api/config";

const mockAuthValue: AuthContextType = {
  status: "AUTHENTICATED",
  user: {
    principal_id: "ops-user-1",
    tenant_id: "tenant-engineering",
    roles: ["operator"],
    permissions: ["read", "write"],
    principal_type: "HUMAN",
    authentication_method: "OIDC",
    display_name: "Jane Operator",
  },
  isAuthenticated: true,
  isLoading: false,
  error: null,
  loginWithBearerToken: vi.fn(),
  loginWithPkceRedirect: vi.fn(),
  loginWithGoogleRedirect: vi.fn(),
  handleAuthCallback: vi.fn(),
  handleIdTokenCallback: vi.fn(),
  logout: vi.fn(),
  clearError: vi.fn(),
  oidcConfig: {
    ...OIDC_CONFIG,
    isConfigured: true,
  },
};

vi.mock("../context/AuthContext", () => ({
  useAuth: () => mockAuthValue,
}));

describe("WorkflowInstancePage Integration", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockAuthValue.user = {
      principal_id: "ops-user-1",
      tenant_id: "tenant-engineering",
      roles: ["operator"],
      permissions: ["read", "write"],
      principal_type: "HUMAN",
      authentication_method: "OIDC",
      display_name: "Jane Operator",
    };
  });

  const mockInstance: WorkflowInstanceResponse = {
    id: "wf-inst-101",
    tenant_id: "tenant-engineering",
    definition_id: "def-001-uuid",
    workflow_key: "exam-scheduling-flow",
    version: 1,
    business_reference: "EXAM-2026-FALL",
    correlation_id: "corr-101",
    engine_instance_id: "eng-inst-101",
    status: "RUNNING",
    input_variables: {
      plan_id: "plan-101",
      priority: "HIGH",
    },
    started_by: "ops-user-1",
    started_at: "2026-10-25T10:00:00Z",
    completed_at: null,
    created_at: "2026-10-25T10:00:00Z",
    updated_at: "2026-10-25T10:00:00Z",
  };

  const mockHistoryEntries: WorkflowHistoryEntry[] = [
    {
      activity_id: "start-event-1",
      activity_name: "Start Event",
      activity_type: "startEvent",
      start_time: "2026-10-25T10:00:00Z",
      end_time: "2026-10-25T10:00:01Z",
      duration_ms: 1000,
      completed: true,
    },
    {
      activity_id: "user-task-1",
      activity_name: "Approve Faculty Allocation",
      activity_type: "userTask",
      start_time: "2026-10-25T10:05:00Z",
      end_time: null,
      duration_ms: null,
      completed: false,
    },
  ];

  const mockTasks: WorkflowTask[] = [
    {
      id: "task-human-1",
      tenant_id: "tenant-engineering",
      workflow_instance_id: "wf-inst-101",
      task_definition_key: "approve-faculty",
      name: "Approve Faculty Allocation",
      assignee: "ops-user-1",
      candidate_roles: ["operator"],
      status: "PENDING",
      created_at: "2026-10-25T10:05:00Z",
    },
  ];

  it("renders loading state initially while fetching instance and history", () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockReturnValue(new Promise(() => {}));
    vi.spyOn(apiClient, "getWorkflowHistory").mockReturnValue(new Promise(() => {}));
    vi.spyOn(apiClient, "listWorkflowTasks").mockReturnValue(new Promise(() => {}));

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-101"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText(/Loading workflow instance execution details\.\.\./i)).toBeInTheDocument();
  });

  it("renders workflow instance lifecycle and metadata successfully", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockResolvedValue(mockInstance);
    vi.spyOn(apiClient, "getWorkflowHistory").mockResolvedValue(mockHistoryEntries);
    vi.spyOn(apiClient, "listWorkflowTasks").mockResolvedValue(mockTasks);

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-101"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getAllByText("exam-scheduling-flow").length).toBeGreaterThanOrEqual(1);
    });

    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByTitle("EXAM-2026-FALL")).toBeInTheDocument();
    expect(screen.getByText("Start Event")).toBeInTheDocument();
  });

  it("renders chronological execution history and activity telemetry", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockResolvedValue(mockInstance);
    vi.spyOn(apiClient, "getWorkflowHistory").mockResolvedValue(mockHistoryEntries);
    vi.spyOn(apiClient, "listWorkflowTasks").mockResolvedValue(mockTasks);

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-101"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Normalized Activity Execution Trail")).toBeInTheDocument();
    });

    expect(screen.getByText("Start Event")).toBeInTheDocument();
    expect(screen.getByText("Human User Task")).toBeInTheDocument();
    expect(screen.getByText("Approve Faculty Allocation")).toBeInTheDocument();
  });

  it("renders error state when instance is not found (404) or API fails and does not issue secondary requests", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockRejectedValue(new Error("Workflow instance not found or access denied"));
    const historySpy = vi.spyOn(apiClient, "getWorkflowHistory");
    const tasksSpy = vi.spyOn(apiClient, "listWorkflowTasks");

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-404"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Workflow instance not found or access denied/i)).toBeInTheDocument();
    });

    const retryBtn = screen.getByRole("button", { name: /Retry/i });
    expect(retryBtn).toBeInTheDocument();

    // Verify secondary requests were strictly NOT issued
    expect(historySpy).not.toHaveBeenCalled();
    expect(tasksSpy).not.toHaveBeenCalled();
  });

  it("handles forbidden access / missing tenant cleanly without issuing secondary requests", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockRejectedValue(new Error("Forbidden: tenant mismatch"));
    const historySpy = vi.spyOn(apiClient, "getWorkflowHistory");
    const tasksSpy = vi.spyOn(apiClient, "listWorkflowTasks");

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-999"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Forbidden: tenant mismatch/i)).toBeInTheDocument();
    });

    expect(historySpy).not.toHaveBeenCalled();
    expect(tasksSpy).not.toHaveBeenCalled();
  });

  it("handles partial failure: instance succeeds but history fails with 403 Forbidden", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockResolvedValue(mockInstance);
    const historySpy = vi.spyOn(apiClient, "getWorkflowHistory").mockRejectedValue(
      new ApiError(403, "Forbidden: Insufficient privileges to view execution trail")
    );
    vi.spyOn(apiClient, "listWorkflowTasks").mockResolvedValue(mockTasks);

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-101"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getAllByText("exam-scheduling-flow").length).toBeGreaterThanOrEqual(1);
    });

    // Verify top degradation alert is displayed
    expect(screen.getByText(/Partial Data Warning/i)).toBeInTheDocument();
    expect(screen.getByText(/One or more secondary data streams failed to load/i)).toHaveTextContent("Activity History");

    // Verify history tab displays explicit error card with code and retry action
    expect(screen.getByText(/Activity Execution History Unavailable \(HTTP 403\)/i)).toBeInTheDocument();
    const retryHistoryBtn = screen.getByRole("button", { name: /Retry History/i });
    expect(retryHistoryBtn).toBeInTheDocument();

    // Verify retry button re-invokes API
    historySpy.mockResolvedValueOnce(mockHistoryEntries);
    fireEvent.click(retryHistoryBtn);

    await waitFor(() => {
      expect(screen.getByText("Normalized Activity Execution Trail")).toBeInTheDocument();
    });
  });

  it("handles partial failure: instance succeeds but human tasks fail with network error", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockResolvedValue(mockInstance);
    vi.spyOn(apiClient, "getWorkflowHistory").mockResolvedValue(mockHistoryEntries);
    const tasksSpy = vi.spyOn(apiClient, "listWorkflowTasks").mockRejectedValue(
      new Error("Network Error: Failed to reach workflow gateway")
    );

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-101"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getAllByText("exam-scheduling-flow").length).toBeGreaterThanOrEqual(1);
    });

    // Verify degradation alert mentions Human Tasks
    expect(screen.getByText(/Partial Data Warning/i)).toBeInTheDocument();
    expect(screen.getByText(/One or more secondary data streams failed to load/i)).toHaveTextContent("Human Tasks");

    // Switch to Tasks tab
    const tasksTab = screen.getByRole("button", { name: /Human Tasks/i });
    fireEvent.click(tasksTab);

    await waitFor(() => {
      expect(screen.getByText(/Workflow Human Tasks Unavailable/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/Network Error: Unable to communicate with the workflow API/i)).toBeInTheDocument();

    const retryTasksBtn = screen.getByRole("button", { name: /Retry Tasks/i });
    expect(retryTasksBtn).toBeInTheDocument();

    // Verify retry button re-invokes tasks API
    tasksSpy.mockResolvedValueOnce(mockTasks);
    fireEvent.click(retryTasksBtn);

    await waitFor(() => {
      expect(screen.getByText("Approve Faculty Allocation")).toBeInTheDocument();
    });
  });

  it("distinguishes genuine empty history from error states", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockResolvedValue(mockInstance);
    vi.spyOn(apiClient, "getWorkflowHistory").mockResolvedValue([]);
    vi.spyOn(apiClient, "listWorkflowTasks").mockResolvedValue(mockTasks);

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-101"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getAllByText("exam-scheduling-flow").length).toBeGreaterThanOrEqual(1);
    });

    // Should NOT show partial degradation warning
    expect(screen.queryByText(/Partial Data Warning/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Zero activity execution records captured for this instance yet\./i)).toBeInTheDocument();
  });

  it("distinguishes genuine absence of tasks from error states", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockResolvedValue(mockInstance);
    vi.spyOn(apiClient, "getWorkflowHistory").mockResolvedValue(mockHistoryEntries);
    vi.spyOn(apiClient, "listWorkflowTasks").mockResolvedValue([]);

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-101"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getAllByText("exam-scheduling-flow").length).toBeGreaterThanOrEqual(1);
    });

    const tasksTab = screen.getByRole("button", { name: /Human Tasks/i });
    fireEvent.click(tasksTab);

    await waitFor(() => {
      expect(screen.getByText(/Zero active human tasks assigned to this workflow instance\./i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Workflow Human Tasks Unavailable/i)).not.toBeInTheDocument();
  });

  it("display-redacts nested sensitive variables in process variables tab", async () => {
    const sensitiveInstance: WorkflowInstanceResponse = {
      ...mockInstance,
      input_variables: {
        plan_id: "plan-101",
        operational_target: "fall-2026",
        db_connection: {
          client_secret: "super-secret-key-12345",
          password: "mysecretpassword",
          api_key: "key-999-unredacted",
          nested_tokens: {
            access_token: "jwt.access.token",
          },
        },
      },
    };

    vi.spyOn(apiClient, "getWorkflowInstance").mockResolvedValue(sensitiveInstance);
    vi.spyOn(apiClient, "getWorkflowHistory").mockResolvedValue(mockHistoryEntries);
    vi.spyOn(apiClient, "listWorkflowTasks").mockResolvedValue(mockTasks);

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-101"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getAllByText("exam-scheduling-flow").length).toBeGreaterThanOrEqual(1);
    });

    const varsTab = screen.getByRole("button", { name: /Process Variables/i });
    fireEvent.click(varsTab);

    await waitFor(() => {
      expect(screen.getByText(/Process Variables \(Display Redacted\)/i)).toBeInTheDocument();
    });

    // Sensitive values must NOT appear
    expect(screen.queryByText(/super-secret-key-12345/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/mysecretpassword/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/key-999-unredacted/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/jwt\.access\.token/i)).not.toBeInTheDocument();

    // Permitted operational variables must appear
    expect(screen.getByText(/"plan_id": "plan-101"/i)).toBeInTheDocument();
    expect(screen.getByText(/"operational_target": "fall-2026"/i)).toBeInTheDocument();
  });

  it("fails closed when tenant identity is missing", async () => {
    mockAuthValue.user = {
      ...mockAuthValue.user!,
      tenant_id: "",
    };
    const instSpy = vi.spyOn(apiClient, "getWorkflowInstance");

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-101"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Active authenticated tenant context is required/i)).toBeInTheDocument();
    });

    expect(instSpy).not.toHaveBeenCalled();
  });

  it("strictly suppresses secondary requests when primary instance returns HTTP 403", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockRejectedValue(
      new ApiError(403, "Forbidden: Cross-tenant access denied")
    );
    const historySpy = vi.spyOn(apiClient, "getWorkflowHistory");
    const tasksSpy = vi.spyOn(apiClient, "listWorkflowTasks");

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-forbidden"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Tenant Boundary Enforced/i)).toBeInTheDocument();
    });

    expect(historySpy).not.toHaveBeenCalled();
    expect(tasksSpy).not.toHaveBeenCalled();
  });

  it("strictly suppresses secondary requests when primary instance returns HTTP 404", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockRejectedValue(
      new ApiError(404, "Workflow instance not found")
    );
    const historySpy = vi.spyOn(apiClient, "getWorkflowHistory");
    const tasksSpy = vi.spyOn(apiClient, "listWorkflowTasks");

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-missing"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Instance Not Found" })).toBeInTheDocument();
    });

    expect(historySpy).not.toHaveBeenCalled();
    expect(tasksSpy).not.toHaveBeenCalled();
  });

  it("strictly suppresses secondary requests when primary instance returns HTTP 500", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockRejectedValue(
      new ApiError(500, "Internal workflow database failure")
    );
    const historySpy = vi.spyOn(apiClient, "getWorkflowHistory");
    const tasksSpy = vi.spyOn(apiClient, "listWorkflowTasks");

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-err"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Server Error \(HTTP 500\)/i)).toBeInTheDocument();
    });

    expect(historySpy).not.toHaveBeenCalled();
    expect(tasksSpy).not.toHaveBeenCalled();
  });

  it("strictly suppresses secondary requests when primary instance encounters network failure", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockRejectedValue(
      new Error("Failed to fetch: Network unreachable")
    );
    const historySpy = vi.spyOn(apiClient, "getWorkflowHistory");
    const tasksSpy = vi.spyOn(apiClient, "listWorkflowTasks");

    render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-neterr"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Network Error: Unable to communicate with the workflow API/i)).toBeInTheDocument();
    });

    expect(historySpy).not.toHaveBeenCalled();
    expect(tasksSpy).not.toHaveBeenCalled();
  });

  it("discards stale secondary responses if instance shifts before request completes", async () => {
    let resolveSlowHistory: (val: WorkflowHistoryEntry[]) => void = () => {};
    const slowHistoryPromise = new Promise<WorkflowHistoryEntry[]>((resolve) => {
      resolveSlowHistory = resolve;
    });

    vi.spyOn(apiClient, "getWorkflowInstance").mockResolvedValue(mockInstance);
    vi.spyOn(apiClient, "getWorkflowHistory").mockReturnValue(slowHistoryPromise);
    vi.spyOn(apiClient, "listWorkflowTasks").mockResolvedValue(mockTasks);

    const { unmount } = render(
      <MemoryRouter initialEntries={["/workflows/wf-inst-101"]}>
        <Routes>
          <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getAllByText("exam-scheduling-flow").length).toBeGreaterThanOrEqual(1);
    });

    // Unmount before slow history finishes
    unmount();

    // Resolving slow history after unmount should not cause uncaught errors
    resolveSlowHistory(mockHistoryEntries);
  });
});
