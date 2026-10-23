import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { DashboardPage } from "../pages/DashboardPage";
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

describe("DashboardPage Real Analytics Integration", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders loading state while fetching dashboard telemetry", () => {
    vi.spyOn(apiClient, "getOperationsDashboard").mockReturnValue(new Promise(() => {}));

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    );

    expect(
      screen.getByText(/Querying live operational analytics from database\.\.\./i)
    ).toBeInTheDocument();
  });

  it("renders operational KPI metrics when API call succeeds", async () => {
    const mockSummary: OperationsDashboardSummary = {
      tenant_id: "tenant-engineering",
      plan_progress: {
        plan_id: "plan-101",
        plan_name: "Semester 1 Examination Schedule Plan",
        total_tasks: 45,
        completed_tasks: 30,
        progress_percentage: 67,
        current_milestone: "Phase-A Completion",
        status: "IN_PROGRESS",
      },
      blockers: [
        {
          task_id: "task-001",
          temporary_id: "T-01",
          title: "Auditorium Sound System Inspection",
          blocked_reason: "Awaiting facilities vendor access",
        },
      ],
      sla_state: {
        status: "HEALTHY",
        active_workflows_count: 5,
        warning_count: 1,
        breached_count: 0,
      },
      schedule_health: {
        schedule_id: "sched-501",
        version: 3,
        status: "FEASIBLE",
        hard_violations: 0,
        solver_status: "OPTIMAL",
        calendar_sync_status: "SYNCED",
      },
      repair_movement: {
        latest_repair_id: "rep-002",
        moved_assignments_count: 2,
        locked_assignments_moved: 0,
        repair_reason: "Room 101 water pipe maintenance",
      },
      recent_events: [
        {
          event_type: "SCHEDULE_OPTIMIZED",
          resource_type: "schedule",
          resource_id: "sched-501",
          outcome: "SUCCESS",
          recorded_at: "2026-10-19T21:15:00Z",
        },
      ],
      risk_summary: {
        open_risks_count: 0,
        risks_by_severity: {},
        stale_resources_count: 0,
        repair_queue_depth: 0,
        repair_stability: {
          total_repairs_count: 1,
          average_percentage_moved: 3.2,
          locked_moved_count: 0,
        },
      },
    };

    vi.spyOn(apiClient, "getOperationsDashboard").mockResolvedValueOnce(mockSummary);

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Operations Overview")).toBeInTheDocument();
      expect(screen.getByText("67%")).toBeInTheDocument(); // progress_percentage
      expect(screen.getByText("Semester 1 Examination Schedule Plan")).toBeInTheDocument();
      expect(screen.getByText("FEASIBLE")).toBeInTheDocument(); // schedule_health.status
      expect(screen.getByText("0 Hard Violations")).toBeInTheDocument();
      expect(screen.getByText("5 Active Workflows")).toBeInTheDocument();
      expect(screen.getByText("1 Warnings")).toBeInTheDocument();
      expect(screen.getByText("Auditorium Sound System Inspection")).toBeInTheDocument();
      expect(screen.getByText("Awaiting facilities vendor access")).toBeInTheDocument();
    });
  });

  it("renders error state when API request fails and provides retry trigger", async () => {
    vi.spyOn(apiClient, "getOperationsDashboard")
      .mockRejectedValueOnce(new Error("Downstream Analytics Gateway unreachable (503)"))
      .mockResolvedValueOnce({
        tenant_id: "tenant-engineering",
        plan_progress: {
          plan_id: null,
          plan_name: null,
          total_tasks: 0,
          completed_tasks: 0,
          progress_percentage: 0,
          current_milestone: null,
          status: "DRAFT",
        },
        blockers: [],
        sla_state: {
          status: "HEALTHY",
          active_workflows_count: 0,
          warning_count: 0,
          breached_count: 0,
        },
        schedule_health: {
          schedule_id: null,
          version: 0,
          status: "NONE",
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
      });

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Downstream Analytics Gateway unreachable/i)).toBeInTheDocument();
    });

    // Click Retry
    const retryBtn = screen.getByRole("button", { name: /Retry/i });
    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(screen.getByText("Operations Overview")).toBeInTheDocument();
      expect(screen.getByText("0%")).toBeInTheDocument();
    });
  });
});
