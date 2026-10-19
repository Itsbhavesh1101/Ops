import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { PlansPage } from "../pages/PlansPage";
import { PlanDetailPage } from "../pages/PlanDetailPage";
import { ToastProvider } from "../components/ui/Toast";
import { apiClient, ApiError } from "../api/client";
import type {
  PlanResponse,
  PlanDetailResponse,
  ValidationResult,
  CPMScheduleResult,
  TaskResponse,
} from "../api/types";

const mockAuthValue = {
  status: "AUTHENTICATED",
  user: {
    principal_id: "test-operator",
    tenant_id: "tenant-engineering",
    roles: ["operator", "approver"],
    permissions: ["read", "write", "plan:approve"],
    principal_type: "HUMAN",
    authentication_method: "OIDC",
    display_name: "Test Operator",
  },
  isAuthenticated: true,
  isLoading: false,
  error: null,
  loginWithBearerToken: vi.fn(),
  logout: vi.fn(),
  clearError: vi.fn(),
  oidcConfig: {
    clientId: "test-client-id",
    issuer: "https://accounts.google.com",
    audience: "e7-pilot-client-id",
    redirectUri: "http://127.0.0.1:5173",
    isConfigured: true,
  },
};

vi.mock("../context/AuthContext", () => ({
  useAuth: () => mockAuthValue,
}));

describe("Plans & WBS Feature Screens (F2)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockAuthValue.user.roles = ["operator", "approver"];
    mockAuthValue.user.permissions = ["read", "write", "plan:approve"];
  });

  const samplePlan: PlanResponse = {
    id: "plan-201",
    goal_id: "goal-101",
    plan_name: "Semester Examination Master Plan",
    status: "DRAFT",
    version_number: 1,
    is_baseline: false,
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-02T12:00:00Z",
  };

  const samplePlanDetail: PlanDetailResponse = {
    id: "plan-201",
    goal_id: "goal-101",
    plan_name: "Semester Examination Master Plan",
    status: "DRAFT",
    version_number: 1,
    is_baseline: false,
    baseline_hash: "hash-99a",
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-02T12:00:00Z",
    tasks: [
      {
        id: "task-001",
        plan_id: "plan-201",
        temporary_id: "T-01",
        title: "Print Exam Papers Securely",
        estimated_duration_minutes: 120,
        progress_percentage: 25,
        status: "IN_PROGRESS",
        blocked_reason: null,
        owner_role: "Exam Controller",
        priority: "HIGH",
        created_at: "2026-10-01T10:00:00Z",
        updated_at: "2026-10-01T10:00:00Z",
      },
    ],
    dependencies: [
      {
        id: "dep-001",
        predecessor_id: "task-001",
        successor_id: "task-002",
        dependency_type: "FS",
        lag_minutes: 0,
      },
    ],
    milestones: [],
    risks: [],
  };

  it("renders plans list with real plan card and click-through navigation", async () => {
    vi.spyOn(apiClient, "listPlans").mockResolvedValue([samplePlan]);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans"]}>
          <Routes>
            <Route path="/plans" element={<PlansPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    expect(screen.getByText(/Fetching operational plans from engine\.\.\./i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination Master Plan")[0]).toBeInTheDocument();
    });

    expect(screen.getByText(/v1/i)).toBeInTheDocument();
  });

  it("renders plan detail with WBS tasks and updates task progress", async () => {
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(samplePlanDetail);
    const mockUpdatedTask: TaskResponse = {
      id: "task-001",
      plan_id: "plan-201",
      temporary_id: "T-01",
      title: "Print Exam Papers Securely",
      progress_percentage: 75,
      status: "IN_PROGRESS",
      blocked_reason: null,
      updated_at: "2026-10-01T12:00:00Z",
    };
    const patchSpy = vi.spyOn(apiClient, "updateTaskProgress").mockResolvedValue(mockUpdatedTask);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-201"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination Master Plan")[0]).toBeInTheDocument();
      expect(screen.getByText("Print Exam Papers Securely")).toBeInTheDocument();
      expect(screen.getByText(/25%/i)).toBeInTheDocument();
    });

    const updateBtn = screen.getByRole("button", { name: /^Update$/i });
    fireEvent.click(updateBtn);

    expect(screen.getByText(/Update Task: Print Exam Papers Securely/i)).toBeInTheDocument();
    const progressInput = screen.getByRole("slider");
    fireEvent.change(progressInput, { target: { value: "75" } });

    const saveBtn = screen.getByRole("button", { name: /Save Progress/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(patchSpy).toHaveBeenCalledWith(
        "task-001",
        expect.objectContaining({ progress_percentage: 75 }),
        "tenant-engineering"
      );
    });
  });

  it("validates plan and displays validation results", async () => {
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(samplePlanDetail);
    const mockValidation: ValidationResult = {
      is_valid: true,
      issues: [
        {
          code: "WARN_LEAD_TIME",
          location: "task-001",
          message: "Task lead time is approaching critical path limit.",
          severity: "WARNING",
        },
      ],
      evaluated_task_count: 1,
      evaluated_dependency_count: 1,
    };
    const validateSpy = vi.spyOn(apiClient, "validatePlan").mockResolvedValue(mockValidation);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-201"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination Master Plan")[0]).toBeInTheDocument();
    });

    const validateBtn = screen.getByRole("button", { name: /Validate Plan/i });
    fireEvent.click(validateBtn);

    await waitFor(() => {
      expect(validateSpy).toHaveBeenCalledWith("plan-201", "tenant-engineering");
      expect(screen.getByText(/Plan Validated Successfully/i)).toBeInTheDocument();
      expect(screen.getByText(/Task lead time is approaching critical path limit\./i)).toBeInTheDocument();
    });
  });

  const validatedPlanDetail: PlanDetailResponse = {
    ...samplePlanDetail,
    status: "VALIDATED",
  };

  const approvalPendingPlanDetail: PlanDetailResponse = {
    ...samplePlanDetail,
    status: "APPROVAL_PENDING",
  };

  const approvedPlanDetail: PlanDetailResponse = {
    ...samplePlanDetail,
    status: "APPROVED",
    is_baseline: true,
  };

  it("submits validated plan for approval and refreshes state", async () => {
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(validatedPlanDetail);
    const submitSpy = vi.spyOn(apiClient, "submitPlanForApproval").mockResolvedValue({
      ...validatedPlanDetail,
      status: "APPROVAL_PENDING",
    });

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-201"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination Master Plan")[0]).toBeInTheDocument();
    });

    const submitBtn = screen.getByRole("button", { name: /Submit for Approval/i });
    expect(submitBtn).toBeInTheDocument();
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(submitSpy).toHaveBeenCalledWith("plan-201", "tenant-engineering");
    });
  });

  it("approves plan and transitions status with confirmation dialog", async () => {
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(approvalPendingPlanDetail);
    const approveSpy = vi.spyOn(apiClient, "approvePlan").mockResolvedValue(approvedPlanDetail);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-201"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination Master Plan")[0]).toBeInTheDocument();
    });

    const approveBtn = screen.getByRole("button", { name: /Approve Plan/i });
    fireEvent.click(approveBtn);

    expect(screen.getByText(/Approve and Baseline Plan/i)).toBeInTheDocument();
    const confirmBtn = screen.getByRole("button", { name: /Confirm Approval/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(approveSpy).toHaveBeenCalledWith("plan-201", "tenant-engineering");
    });
  });

  it("renders approved baseline status and hides duplicate approval action", async () => {
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(approvedPlanDetail);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-201"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination Master Plan")[0]).toBeInTheDocument();
      expect(screen.getByText(/Approved Baseline/i)).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: /Approve Plan/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Submit for Approval/i })).not.toBeInTheDocument();
  });

  it("fetches and renders critical path analysis", async () => {
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(samplePlanDetail);
    const mockCPM: CPMScheduleResult = {
      project_duration_minutes: 120,
      critical_path: ["task-001"],
      schedule: {
        "task-001": {
          task_id: "task-001",
          duration_minutes: 120,
          early_start: 0,
          early_finish: 120,
          late_start: 0,
          late_finish: 120,
          total_slack: 0,
          is_critical: true,
        },
      },
    };
    const cpmSpy = vi.spyOn(apiClient, "getCriticalPath").mockResolvedValue(mockCPM);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-201"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination Master Plan")[0]).toBeInTheDocument();
    });

    const cpmTab = screen.getByRole("button", { name: /Critical Path Analysis/i });
    fireEvent.click(cpmTab);

    await waitFor(() => {
      expect(cpmSpy).toHaveBeenCalledWith("plan-201", "tenant-engineering");
      expect(screen.getByText(/120 minutes/i)).toBeInTheDocument();
      expect(screen.getByText(/1 tasks/i)).toBeInTheDocument();
    });
  });

  it("handles 403 unauthorized error when approving plan", async () => {
    mockAuthValue.user.roles = ["operator", "approver"];
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(approvalPendingPlanDetail);
    vi.spyOn(apiClient, "approvePlan").mockRejectedValue(
      new ApiError(403, "Plan approval requires approver role or plan:approve permission.")
    );

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-201"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination Master Plan")[0]).toBeInTheDocument();
    });

    const approveBtn = screen.getByRole("button", { name: /Approve Plan/i });
    fireEvent.click(approveBtn);

    const confirmBtn = screen.getByRole("button", { name: /Confirm Approval/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(screen.getAllByText(/Permission Denied: User role lacks authority/i)[0]).toBeInTheDocument();
    });
  });

  it("handles 422 invalid state transition error when approving plan", async () => {
    mockAuthValue.user.roles = ["operator", "approver"];
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(approvalPendingPlanDetail);
    vi.spyOn(apiClient, "approvePlan").mockRejectedValue(
      new ApiError(422, "Illegal plan state transition from 'APPROVAL_PENDING' to 'APPROVED'.")
    );

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-201"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination Master Plan")[0]).toBeInTheDocument();
    });

    const approveBtn = screen.getByRole("button", { name: /Approve Plan/i });
    fireEvent.click(approveBtn);

    const confirmBtn = screen.getByRole("button", { name: /Confirm Approval/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(screen.getAllByText(/Plan State Violation/i)[0]).toBeInTheDocument();
    });
  });

  it("disables approve button when user lacks approval authority", async () => {
    mockAuthValue.user.roles = ["auditor"];
    mockAuthValue.user.permissions = ["read"];
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(approvalPendingPlanDetail);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-201"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination Master Plan")[0]).toBeInTheDocument();
    });

    const approveBtn = screen.getByRole("button", { name: /Approve Plan/i });
    expect(approveBtn).toBeDisabled();
    expect(approveBtn).toHaveAttribute("title", "Approval requires approver or tenant_admin role");
  });

  it("disables submit button when user lacks submission authority", async () => {
    mockAuthValue.user.roles = ["auditor"];
    mockAuthValue.user.permissions = ["read"];
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(validatedPlanDetail);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-201"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination Master Plan")[0]).toBeInTheDocument();
    });

    const submitBtn = screen.getByRole("button", { name: /Submit for Approval/i });
    expect(submitBtn).toBeDisabled();
    expect(submitBtn).toHaveAttribute("title", "Submission requires planner or tenant_admin role");
  });
});
