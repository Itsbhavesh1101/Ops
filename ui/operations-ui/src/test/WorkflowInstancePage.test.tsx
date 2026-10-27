import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { WorkflowInstancePage } from "../pages/WorkflowInstancePage";
import { AuthContextType } from "../context/AuthContext";
import { apiClient } from "../api/client";
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

  it("renders error state when instance is not found (404) or API fails", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockRejectedValue(new Error("Workflow instance not found or access denied"));
    vi.spyOn(apiClient, "getWorkflowHistory").mockRejectedValue(new Error("Workflow instance not found or access denied"));
    vi.spyOn(apiClient, "listWorkflowTasks").mockResolvedValue([]);

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
  });

  it("handles forbidden access / missing tenant cleanly", async () => {
    vi.spyOn(apiClient, "getWorkflowInstance").mockRejectedValue(new Error("Forbidden: tenant mismatch"));
    vi.spyOn(apiClient, "getWorkflowHistory").mockResolvedValue([]);
    vi.spyOn(apiClient, "listWorkflowTasks").mockResolvedValue([]);

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
  });
});
