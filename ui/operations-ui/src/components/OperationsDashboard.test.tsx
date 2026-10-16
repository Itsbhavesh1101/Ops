import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { OperationsDashboard } from "./OperationsDashboard";
import { OperationsDashboardSummary, WorkflowApiClient } from "../api/client";

describe("OperationsDashboard Component", () => {
  let mockApiClient: WorkflowApiClient;
  const mockSummary: OperationsDashboardSummary = {
    tenant_id: "tenant-engineering",
    plan_progress: {
      plan_id: "plan-123",
      plan_name: "Grade 10 Semester Examinations",
      total_tasks: 10,
      completed_tasks: 6,
      progress_percentage: 60,
      current_milestone: "Published Schedule & Notifications Emitted",
      status: "APPROVED",
    },
    blockers: [],
    sla_state: {
      status: "HEALTHY",
      active_workflows_count: 1,
      warning_count: 0,
      breached_count: 0,
    },
    schedule_health: {
      schedule_id: "sched-456",
      version: 1,
      status: "PUBLISHED",
      hard_violations: 0,
      solver_status: "FEASIBLE",
      calendar_sync_status: "SYNCED",
    },
    repair_movement: {
      latest_repair_id: null,
      moved_assignments_count: 0,
      locked_assignments_moved: 0,
      repair_reason: null,
    },
    recent_events: [
      {
        event_type: "schedule.published",
        resource_type: "schedule",
        resource_id: "sched-456",
        outcome: "SUCCESS",
        recorded_at: "2026-10-06T08:00:00Z",
        correlation_id: "corr-789",
      },
    ],
    risk_summary: {
      open_risks_count: 2,
      risks_by_severity: { CRITICAL: 1, HIGH: 1, MEDIUM: 0, LOW: 0 },
      stale_resources_count: 1,
      repair_queue_depth: 3,
      repair_stability: {
        total_repairs_count: 2,
        average_percentage_moved: 12.5,
        locked_moved_count: 0,
      },
    },
  };

  beforeEach(() => {
    mockApiClient = new WorkflowApiClient();
    mockApiClient.getOperationsDashboard = vi.fn().mockResolvedValue(mockSummary);
  });

  it("renders operations dashboard with all 5 core metric areas", async () => {
    render(<OperationsDashboard apiClient={mockApiClient} tenantId="tenant-engineering" />);

    await waitFor(() => {
      expect(screen.getByText("Grade 10 Semester Examinations")).toBeInTheDocument();
      expect(screen.getByText("60%")).toBeInTheDocument();
      expect(screen.getByText("SLA: HEALTHY")).toBeInTheDocument();
      expect(screen.getByText("PUBLISHED")).toBeInTheDocument();
      expect(screen.getByText("Cal: SYNCED")).toBeInTheDocument();
      expect(screen.getByText("schedule.published")).toBeInTheDocument();
    });
  });

  it("renders Phase-7 risk monitor and repair stability metrics", async () => {
    render(<OperationsDashboard apiClient={mockApiClient} tenantId="tenant-engineering" />);

    await waitFor(() => {
      expect(
        screen.getByText("Phase-7 Real-Time Risk Monitor & Controlled Repair Queue")
      ).toBeInTheDocument();
      expect(screen.getByText("2 Active")).toBeInTheDocument();
      expect(screen.getByText("Queue: 3")).toBeInTheDocument();
      expect(screen.getByText("12.5%")).toBeInTheDocument();
      expect(screen.getByText("0 (Zero)")).toBeInTheDocument();
    });
  });

  it("handles loading error gracefully without crashing", async () => {
    mockApiClient.getOperationsDashboard = vi.fn().mockRejectedValueOnce(
      new Error("Failed to communicate with operations engine API")
    );

    render(<OperationsDashboard apiClient={mockApiClient} tenantId="tenant-engineering" />);

    await waitFor(() => {
      expect(
        screen.getByText("Failed to communicate with operations engine API")
      ).toBeInTheDocument();
    });
  });
});
