import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { PlanDetailPage } from "../pages/PlanDetailPage";
import { TasksPage } from "../pages/TasksPage";
import { ToastProvider } from "../components/ui/Toast";
import { AuthContextType } from "../context/AuthContext";
import { apiClient } from "../api/client";
import type {
  PlanResponse,
  PlanDetailResponse,
  PlanRecommendationsResponse,
  MilestoneHealthResponse,
  TaskRiskResponse,
  TaskDetailResponse,
} from "../api/types";
import { OIDC_CONFIG } from "../api/config";

const mockAuthValue: AuthContextType = {
  status: "AUTHENTICATED",
  user: {
    principal_id: "lead-planner-1",
    tenant_id: "tenant-engineering",
    roles: ["planner", "operator"],
    permissions: ["read", "write"],
    principal_type: "HUMAN",
    authentication_method: "OIDC",
    display_name: "Patricia Planner",
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

describe("DQ-2 Task Intelligence Frontend Integration", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const mockPlanDetail: PlanDetailResponse = {
    id: "plan-dq2-1",
    goal_id: "goal-1",
    plan_name: "Autonomous Campus Operations Rollout",
    version_number: 1,
    status: "APPROVED",
    is_baseline: true,
    created_at: "2026-10-25T10:00:00Z",
    updated_at: "2026-10-25T10:00:00Z",
    tasks: [
      {
        id: "task-001",
        plan_id: "plan-dq2-1",
        temporary_id: "T-101",
        title: "Hardware Telemetry Integration",
        description: "Connect IoT sensors to message bus",
        estimated_duration_minutes: 240,
        actual_duration_minutes: 330,
        variance_minutes: 90,
        completion_evidence: "Bus ping verified via mqtt broker",
        status: "IN_PROGRESS",
        progress_percentage: 50,
        priority: "HIGH",
        created_at: "2026-10-25T10:00:00Z",
        updated_at: "2026-10-25T10:00:00Z",
      },
      {
        id: "task-002",
        plan_id: "plan-dq2-1",
        temporary_id: "T-102",
        title: "Database Index Tuning",
        description: "Tune partition keys for timetable queries",
        estimated_duration_minutes: 120,
        variance_minutes: 0,
        status: "TODO",
        progress_percentage: 0,
        priority: "MEDIUM",
        created_at: "2026-10-25T10:00:00Z",
        updated_at: "2026-10-25T10:00:00Z",
      },
    ],
    milestones: [
      {
        id: "ms-1",
        temporary_id: "MS-1",
        name: "Phase 1: Ingestion Infrastructure",
        target_date: "2026-11-01T00:00:00Z",
      },
    ],
    dependencies: [],
    risks: [],
  };

  const mockRecommendations: PlanRecommendationsResponse = {
    plan_id: "plan-dq2-1",
    tenant_id: "tenant-engineering",
    total_tasks: 2,
    ready_tasks_count: 2,
    blocked_tasks_count: 0,
    completed_tasks_count: 0,
    is_authoritative: false,
    policy_applied: true,
    cpm_diagnostic: "CPM graph acyclic. Critical path duration: 12.0 hours.",
    recommendations: [
      {
        task_id: "task-001",
        temporary_id: "T-101",
        title: "Hardware Telemetry Integration",
        rank: 1,
        score: 0.95,
        action: "CONTINUE",
        priority: "HIGH",
        status: "IN_PROGRESS",
        is_critical_path: true,
        total_slack_minutes: 0,
        quality: "HIGH",
        quality_basis: "CPM critical path",
        explanation: "Task is on the critical path and actively in progress (50%). Completing it unblocks downstream database tuning.",
      },
      {
        task_id: "task-002",
        temporary_id: "T-102",
        title: "Database Index Tuning",
        rank: 2,
        score: 0.72,
        action: "START_NOW",
        priority: "MEDIUM",
        status: "TODO",
        is_critical_path: true,
        total_slack_minutes: 0,
        quality: "MEDIUM",
        quality_basis: "Ready for start",
        explanation: "Pre-stage environment for index tuning while waiting for telemetry.",
      },
    ],
  };

  const mockMilestonesHealth: MilestoneHealthResponse[] = [
    {
      id: "ms-1",
      tenant_id: "tenant-engineering",
      plan_id: "plan-dq2-1",
      temporary_id: "MS-1",
      name: "Phase 1: Ingestion Infrastructure",
      target_date: "2026-11-01T00:00:00Z",
      health: "ON_TRACK",
      progress_percentage: 50,
      completed_tasks: 0,
      total_tasks: 2,
      blocked_tasks: 0,
      is_overdue: false,
    },
  ];

  const mockRisks: TaskRiskResponse[] = [
    {
      task_id: "task-001",
      category: "DURATION_VARIANCE",
      severity: "MEDIUM",
      description: "Task actual duration has exceeded baseline estimate by 90 minutes.",
      detected_at: "2026-10-25T11:30:00Z",
      is_evaluable: true,
    },
  ];

  it("renders recommendations tab with non-authoritative advisory disclaimer", async () => {
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(mockPlanDetail);
    vi.spyOn(apiClient, "getPlanRecommendations").mockResolvedValue(mockRecommendations);
    vi.spyOn(apiClient, "getPlanMilestones").mockResolvedValue(mockMilestonesHealth);
    vi.spyOn(apiClient, "getPlanRisks").mockResolvedValue(mockRisks);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-dq2-1"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Autonomous Campus Operations Rollout")).toBeInTheDocument();
    });

    // Switch to Recommendations tab
    const recoTab = screen.getByRole("button", { name: /Recommendations/i });
    fireEvent.click(recoTab);

    // Verify advisory banner
    await waitFor(() => {
      expect(screen.getByText(/Advisory Next-Best-Task Intelligence/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/strictly advisory/i)).toBeInTheDocument();
    expect(screen.getByText(/Human operational judgment is required/i)).toBeInTheDocument();

    // Verify recommendations content
    expect(screen.getAllByText("Hardware Telemetry Integration").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("CONTINUE")).toBeInTheDocument();
    expect(screen.getByText("START NOW")).toBeInTheDocument();
    expect(screen.getAllByText("Critical Path").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("HIGH Confidence")).toBeInTheDocument();
    expect(screen.getByText("Score: 0.950")).toBeInTheDocument();
    expect(screen.getByText(/Task is on the critical path and actively in progress/i)).toBeInTheDocument();

    // Verify CPM diagnostic
    expect(screen.getByText(/CPM graph acyclic\. Critical path duration: 12\.0 hours\./i)).toBeInTheDocument();
  });

  it("renders derived milestone health and risk presentation", async () => {
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(mockPlanDetail);
    vi.spyOn(apiClient, "getPlanRecommendations").mockResolvedValue(mockRecommendations);
    vi.spyOn(apiClient, "getPlanMilestones").mockResolvedValue(mockMilestonesHealth);
    vi.spyOn(apiClient, "getPlanRisks").mockResolvedValue(mockRisks);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-dq2-1"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Autonomous Campus Operations Rollout")).toBeInTheDocument();
    });

    // Test Milestones Tab
    const msTab = screen.getByRole("button", { name: /Milestones/i });
    fireEvent.click(msTab);

    await waitFor(() => {
      expect(screen.getByText("Phase 1: Ingestion Infrastructure")).toBeInTheDocument();
    });

    expect(screen.getByText("ON TRACK")).toBeInTheDocument();

    // Test Risks Tab
    const risksTab = screen.getByRole("button", { name: /Risks/i });
    fireEvent.click(risksTab);

    await waitFor(() => {
      expect(screen.getByText("MEDIUM SEVERITY")).toBeInTheDocument();
    });

    expect(screen.getByText(/Category: DURATION_VARIANCE/i)).toBeInTheDocument();
    expect(screen.getByText(/Task actual duration has exceeded baseline estimate/i)).toBeInTheDocument();
  });

  it("displays duration variance and completion evidence in WBS task table", async () => {
    vi.spyOn(apiClient, "getPlan").mockResolvedValue(mockPlanDetail);
    vi.spyOn(apiClient, "getPlanRecommendations").mockResolvedValue(mockRecommendations);
    vi.spyOn(apiClient, "getPlanMilestones").mockResolvedValue(mockMilestonesHealth);
    vi.spyOn(apiClient, "getPlanRisks").mockResolvedValue(mockRisks);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/plans/plan-dq2-1"]}>
          <Routes>
            <Route path="/plans/:planId" element={<PlanDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Autonomous Campus Operations Rollout")).toBeInTheDocument();
    });

    // In WBS table:
    expect(screen.getByText("+90m")).toBeInTheDocument();
    expect(screen.getByText(/Evidence: Bus ping verified via mqtt broker/i)).toBeInTheDocument();
  });

  it("renders TasksPage with variance and completion evidence fields", async () => {
    const mockPlans: PlanResponse[] = [
      {
        id: "plan-dq2-1",
        goal_id: "goal-1",
        plan_name: "Autonomous Campus Operations Rollout",
        status: "APPROVED",
        version_number: 1,
        is_baseline: true,
        created_at: "2026-10-25T10:00:00Z",
        updated_at: "2026-10-25T10:00:00Z",
      },
    ];

    const mockTasks: TaskDetailResponse[] = [
      {
        id: "task-001",
        plan_id: "plan-dq2-1",
        temporary_id: "T-101",
        title: "Hardware Telemetry Integration",
        description: "Connect IoT sensors",
        estimated_duration_minutes: 240,
        actual_duration_minutes: 210,
        variance_minutes: -30,
        completion_evidence: "Test report signed",
        status: "IN_PROGRESS",
        progress_percentage: 50,
        priority: "HIGH",
        created_at: "2026-10-25T10:00:00Z",
        updated_at: "2026-10-25T10:00:00Z",
      },
    ];

    vi.spyOn(apiClient, "listPlans").mockResolvedValue(mockPlans);
    vi.spyOn(apiClient, "getPlanTasks").mockResolvedValue(mockTasks);

    render(
      <ToastProvider>
        <MemoryRouter>
          <TasksPage />
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByText("Hardware Telemetry Integration")).toBeInTheDocument();
    });

    expect(screen.getByText("-30m")).toBeInTheDocument();
    expect(screen.getByText(/Evidence: Test report signed/i)).toBeInTheDocument();

    // Open progress modal via Edit button
    const editBtn = screen.getByRole("button", { name: /Edit/i });
    fireEvent.click(editBtn);

    await waitFor(() => {
      expect(screen.getByText("Update Task: Hardware Telemetry Integration")).toBeInTheDocument();
    });

    expect(screen.getByLabelText(/Completion Evidence/i)).toBeInTheDocument();
  });
});
