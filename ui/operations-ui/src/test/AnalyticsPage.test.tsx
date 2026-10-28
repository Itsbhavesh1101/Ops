import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AnalyticsPage } from "../pages/AnalyticsPage";
import { AuthContextType } from "../context/AuthContext";
import { apiClient } from "../api/client";
import type { OperationsDashboardSummary } from "../api/types";
import { OIDC_CONFIG } from "../api/config";

const mockAuthValue: AuthContextType = {
  status: "AUTHENTICATED",
  user: {
    principal_id: "ops-user-1",
    tenant_id: "tenant-engineering",
    roles: ["operator"],
    permissions: ["read"],
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

describe("AnalyticsPage Operations Analytics Integration", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const mockAnalyticsData: OperationsDashboardSummary = {
    tenant_id: "tenant-engineering",
    plan_progress: {
      plan_id: "plan-202",
      plan_name: "Fall Semester Exam Timetable",
      total_tasks: 50,
      completed_tasks: 40,
      progress_percentage: 80,
      current_milestone: "Room Allocation Done",
      status: "IN_PROGRESS",
    },
    blockers: [
      {
        task_id: "task-b1",
        temporary_id: "T-B1",
        title: "Main Hall Power Backup Verification",
        blocked_reason: "Vendor technician delay",
      },
    ],
    sla_state: {
      status: "HEALTHY",
      active_workflows_count: 8,
      warning_count: 2,
      breached_count: 0,
    },
    schedule_health: {
      schedule_id: "sched-303",
      version: 4,
      status: "OPTIMAL",
      hard_violations: 0,
      solver_status: "OPTIMAL",
      calendar_sync_status: "SYNCED",
    },
    risk_summary: {
      open_risks_count: 1,
      stale_resources_count: 0,
      repair_queue_depth: 0,
      risks_by_severity: {
        MEDIUM: 1,
      },
      repair_stability: {
        total_repairs_count: 1,
        average_percentage_moved: 2.5,
        locked_moved_count: 0,
      },
    },
    repair_movement: {
      latest_repair_id: "repair-99",
      moved_assignments_count: 3,
      locked_assignments_moved: 0,
      repair_reason: "Proctor medical leave replacement",
    },
    recent_events: [
      {
        recorded_at: "2026-10-25T11:00:00Z",
        event_type: "SCHEDULE_OPTIMIZED",
        resource_type: "SCHEDULE",
        resource_id: "sched-303",
        outcome: "SUCCESS",
      },
    ],
  };

  it("renders loading state while querying live operational analytics", () => {
    vi.spyOn(apiClient, "getOperationsDashboard").mockReturnValue(new Promise(() => {}));

    render(
      <MemoryRouter>
        <AnalyticsPage />
      </MemoryRouter>
    );

    expect(screen.getByText(/Aggregating operational telemetry from system of record\.\.\./i)).toBeInTheDocument();
  });

  it("renders all 7 core operational metric areas when data returns", async () => {
    vi.spyOn(apiClient, "getOperationsDashboard").mockResolvedValue(mockAnalyticsData);

    render(
      <MemoryRouter>
        <AnalyticsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Fall Semester Exam Timetable")).toBeInTheDocument();
    });

    // Check all metric areas
    expect(screen.getAllByText("80%").length).toBeGreaterThanOrEqual(1); // Plan progress
    expect(screen.getByText("Main Hall Power Backup Verification")).toBeInTheDocument(); // Blockers
    expect(screen.getByText("Vendor technician delay")).toBeInTheDocument();
    expect(screen.getByText("Workflow SLA State")).toBeInTheDocument();
    expect(screen.getByText("Schedule Health")).toBeInTheDocument();
    expect(screen.getByText("Disruption Repair Stability")).toBeInTheDocument();
    expect(screen.getByText("Recent Chronological Operational Events")).toBeInTheDocument();
    expect(screen.getByText("SCHEDULE_OPTIMIZED")).toBeInTheDocument();
  });

  it("allows filtering analytics by goal ID", async () => {
    const spy = vi.spyOn(apiClient, "getOperationsDashboard").mockResolvedValue(mockAnalyticsData);

    render(
      <MemoryRouter>
        <AnalyticsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Fall Semester Exam Timetable")).toBeInTheDocument();
    });

    const goalInput = screen.getByPlaceholderText(/Filter telemetry by Parent Goal/i);
    fireEvent.change(goalInput, { target: { value: "goal-123" } });

    const applyButton = screen.getByRole("button", { name: /Filter/i });
    fireEvent.click(applyButton);

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith("tenant-engineering", "goal-123");
    });
  });

  it("renders honest empty states when no active plan or blockers exist", async () => {
    const emptyData: OperationsDashboardSummary = {
      tenant_id: "tenant-engineering",
      plan_progress: {
        plan_id: null,
        plan_name: null,
        total_tasks: 0,
        completed_tasks: 0,
        progress_percentage: 0,
        current_milestone: null,
        status: null,
      },
      blockers: [],
      sla_state: {
        status: "IDLE",
        active_workflows_count: 0,
        warning_count: 0,
        breached_count: 0,
      },
      schedule_health: {
        schedule_id: null,
        version: 0,
        status: null,
        hard_violations: 0,
        solver_status: null,
        calendar_sync_status: "N/A",
      },
      repair_movement: {
        latest_repair_id: null,
        moved_assignments_count: 0,
        locked_assignments_moved: 0,
        repair_reason: null,
      },
      recent_events: [],
    };
    vi.spyOn(apiClient, "getOperationsDashboard").mockResolvedValue(emptyData);

    render(
      <MemoryRouter>
        <AnalyticsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("No Active Plan in Progress")).toBeInTheDocument();
    });

    expect(screen.getByText(/Zero active operational blockers recorded/i)).toBeInTheDocument();
    expect(screen.getByText(/0 active \(0 breached\)/i)).toBeInTheDocument();
    expect(screen.getByText(/v0 • 0 violations/i)).toBeInTheDocument();
    expect(screen.getByText(/No recent operational events recorded/i)).toBeInTheDocument();
  });

  it("renders error state with retry button upon API failure", async () => {
    vi.spyOn(apiClient, "getOperationsDashboard").mockRejectedValue(new Error("Database connection failure"));

    render(
      <MemoryRouter>
        <AnalyticsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Database connection failure")).toBeInTheDocument();
    });

    const retryBtn = screen.getByRole("button", { name: /Retry/i });
    expect(retryBtn).toBeInTheDocument();
  });
});
