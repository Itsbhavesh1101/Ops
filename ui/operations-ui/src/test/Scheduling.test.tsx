import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SchedulingJobsPage } from "../pages/SchedulingJobsPage";
import { SchedulesPage } from "../pages/SchedulesPage";
import { ScheduleDetailPage } from "../pages/ScheduleDetailPage";
import { ToastProvider } from "../components/ui/Toast";
import { apiClient, ApiError } from "../api/client";
import type {
  ScheduleJobResponse,
  ScheduleListItem,
  ScheduleDetailResponse,
  SchedulePublishResponse,
  ScheduleRepairResponse,
} from "../api/types";

const mockAuthValue = {
  status: "AUTHENTICATED",
  user: {
    principal_id: "test-operator",
    tenant_id: "tenant-engineering",
    roles: ["operator"],
    permissions: ["read", "write"],
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

describe("Scheduling Engine & Timetable Feature Screens (F2)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockAuthValue.user.roles = ["operator", "scheduler", "approver"];
    mockAuthValue.user.permissions = ["read", "write", "schedule:publish", "schedule:repair"];
  });

  const feasibleJob: ScheduleJobResponse = {
    job_id: "job-111-feasible",
    schedule_key: "sem1-exam-grid",
    status: "FEASIBLE",
    input_snapshot_hash: "hash-cp-sat-88a",
    solver_metadata: {},
    created_at: "2026-10-15T09:00:00Z",
    completed_at: "2026-10-15T09:00:05Z",
    diagnostics: [],
  };

  const infeasibleJob: ScheduleJobResponse = {
    job_id: "job-222-infeasible",
    schedule_key: "sem1-exam-grid",
    status: "INFEASIBLE",
    input_snapshot_hash: "hash-cp-sat-99b",
    solver_metadata: {},
    created_at: "2026-10-15T09:00:00Z",
    completed_at: "2026-10-15T09:00:05Z",
    diagnostics: [
      {
        conflict_type: "RESOURCE_OVERLAP_IMPOSSIBILITY",
        message: "Room hall-alpha exceeds maximum concurrent activity constraint.",
      },
    ],
  };

  const sampleScheduleList: ScheduleListItem[] = [
    {
      id: "sched-001",
      schedule_key: "sem1-exam-grid",
      version: 1,
      status: "PUBLISHED",
      source_job_id: "job-111-feasible",
      input_snapshot_hash: "hash-cp-sat-88a",
      published_at: "2026-10-15T09:05:00Z",
      published_by: "test-operator",
      created_at: "2026-10-15T09:00:00Z",
      updated_at: "2026-10-15T09:05:00Z",
    },
  ];

  const sampleScheduleDetail: ScheduleDetailResponse = {
    id: "sched-001",
    schedule_key: "sem1-exam-grid",
    version: 1,
    status: "PUBLISHED",
    source_job_id: "job-111-feasible",
    input_snapshot_hash: "hash-cp-sat-88a",
    published_at: "2026-10-15T09:05:00Z",
    published_by: "test-operator",
    created_at: "2026-10-15T09:00:00Z",
    updated_at: "2026-10-15T09:05:00Z",
    assignments: [
      {
        id: "asgn-001",
        schedule_id: "sched-001",
        activity_id: "exam-session-1",
        resource_id: "hall-alpha",
        start_at: "2026-10-25T09:00:00Z",
        end_at: "2026-10-25T11:00:00Z",
        is_locked: false,
        created_at: "2026-10-15T09:00:00Z",
      },
    ],
  };

  it("submits a scheduling job and displays solver progress", async () => {
    const submitSpy = vi.spyOn(apiClient, "createScheduleJob").mockResolvedValue({
      ...feasibleJob,
      status: "QUEUED",
    });

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/scheduling/jobs"]}>
          <Routes>
            <Route path="/scheduling/jobs" element={<SchedulingJobsPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    const dispatchBtn = screen.getByRole("button", { name: /Dispatch First Job/i });
    fireEvent.click(dispatchBtn);

    expect(screen.getByText(/Dispatch Mathematical Solver Job/i)).toBeInTheDocument();

    const runBtn = screen.getByRole("button", { name: /Run CP-SAT Solver/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(submitSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          schedule_key: "exam-schedule-default",
        }),
        "tenant-engineering"
      );
    });
  });

  it("surfaces infeasible diagnostics when solver proves problem is infeasible", async () => {
    vi.spyOn(apiClient, "createScheduleJob").mockResolvedValue(infeasibleJob);
    vi.spyOn(apiClient, "getScheduleJob").mockResolvedValue(infeasibleJob);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/scheduling/jobs"]}>
          <Routes>
            <Route path="/scheduling/jobs" element={<SchedulingJobsPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    const dispatchBtn = screen.getByRole("button", { name: /Dispatch First Job/i });
    fireEvent.click(dispatchBtn);

    const runBtn = screen.getByRole("button", { name: /Run CP-SAT Solver/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(screen.getByText(/Solver Proved Problem INFEASIBLE/i)).toBeInTheDocument();
      expect(screen.getByText(/RESOURCE_OVERLAP_IMPOSSIBILITY/i)).toBeInTheDocument();
    });
  });

  it("renders timetables list page", async () => {
    vi.spyOn(apiClient, "listSchedules").mockResolvedValue(sampleScheduleList);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/schedules"]}>
          <Routes>
            <Route path="/schedules" element={<SchedulesPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText("sem1-exam-grid")[0]).toBeInTheDocument();
      expect(screen.getAllByText(/PUBLISHED/i)[0]).toBeInTheDocument();
      expect(screen.getByText(/v1/i)).toBeInTheDocument();
    });
  });

  it("renders timetable assignments and executes schedule repair producing candidate version", async () => {
    vi.spyOn(apiClient, "getSchedule").mockResolvedValue(sampleScheduleDetail);
    const mockRepairResult: ScheduleRepairResponse = {
      job_id: "repair-job-99",
      baseline_schedule_id: "sched-001",
      candidate_schedule_id: "sched-002",
      candidate_version: 2,
      status: "COMPLETED",
    };
    const repairSpy = vi.spyOn(apiClient, "repairSchedule").mockResolvedValue(mockRepairResult);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/schedules/sched-001"]}>
          <Routes>
            <Route path="/schedules/:scheduleId" element={<ScheduleDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /sem1-exam-grid \(v1\)/i })).toBeInTheDocument();
      expect(screen.getByText("exam-session-1")).toBeInTheDocument();
      expect(screen.getAllByText(/hall-alpha/i)[0]).toBeInTheDocument();
    });

    // Click Repair Schedule
    const repairBtn = screen.getByRole("button", { name: /Repair Schedule/i });
    fireEvent.click(repairBtn);

    expect(screen.getByRole("heading", { name: /Execute Operational Schedule Repair/i })).toBeInTheDocument();

    const submitRepairBtn = screen.getByRole("button", { name: /Run CP-SAT Repair/i });
    fireEvent.click(submitRepairBtn);

    await waitFor(() => {
      expect(repairSpy).toHaveBeenCalledWith(
        "sched-001",
        expect.objectContaining({
          reason: "Invigilator illness disruption",
        }),
        "tenant-engineering"
      );
      expect(screen.getByText(/Repair Job Initiated Successfully/i)).toBeInTheDocument();
    });
  });

  it("publishes candidate schedule with confirmation dialog", async () => {
    const unpublishedSchedule: ScheduleDetailResponse = {
      ...sampleScheduleDetail,
      status: "DRAFT",
      published_at: null,
      published_by: null,
    };
    vi.spyOn(apiClient, "getSchedule").mockResolvedValue(unpublishedSchedule);
    const mockPubResp: SchedulePublishResponse = {
      schedule_id: "sched-001",
      version: 1,
      status: "PUBLISHED",
      published_at: "2026-10-15T10:00:00Z",
      published_by: "test-operator",
      assignment_count: 1,
    };
    const pubSpy = vi.spyOn(apiClient, "publishScheduleJob").mockResolvedValue(mockPubResp);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/schedules/sched-001"]}>
          <Routes>
            <Route path="/schedules/:scheduleId" element={<ScheduleDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Publish Timetable/i })).toBeInTheDocument();
    });

    const pubBtn = screen.getByRole("button", { name: /Publish Timetable/i });
    fireEvent.click(pubBtn);

    expect(screen.getByRole("heading", { name: /Publish Timetable Schedule/i })).toBeInTheDocument();
    const modal = screen.getByRole("dialog");
    const confirmBtn = within(modal).getByRole("button", { name: /Publish Timetable/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(pubSpy).toHaveBeenCalledWith(
        "job-111-feasible",
        { principal: "test-operator" },
        "tenant-engineering"
      );
    });
  });

  it("handles 403 unauthorized error when publishing schedule", async () => {
    const unpublishedSchedule: ScheduleDetailResponse = {
      ...sampleScheduleDetail,
      status: "DRAFT",
      published_at: null,
      published_by: null,
    };
    vi.spyOn(apiClient, "getSchedule").mockResolvedValue(unpublishedSchedule);
    vi.spyOn(apiClient, "publishScheduleJob").mockRejectedValue(
      new ApiError(403, "schedule_publish.unauthorized: Principal is not authorized to publish this schedule.")
    );

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/schedules/sched-001"]}>
          <Routes>
            <Route path="/schedules/:scheduleId" element={<ScheduleDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Publish Timetable/i })).toBeInTheDocument();
    });

    const pubBtn = screen.getByRole("button", { name: /Publish Timetable/i });
    fireEvent.click(pubBtn);

    const modal = screen.getByRole("dialog");
    const confirmBtn = within(modal).getByRole("button", { name: /Publish Timetable/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(screen.getByText(/Unauthorized: Principal or policy does not grant schedule:publish authority/i)).toBeInTheDocument();
    });
  });

  it("handles 400 not_published prerequisite failure on schedule repair", async () => {
    vi.spyOn(apiClient, "getSchedule").mockResolvedValue({
      ...sampleScheduleDetail,
      status: "DRAFT",
      published_at: null,
    });
    vi.spyOn(apiClient, "repairSchedule").mockRejectedValue(
      new ApiError(400, "Schedule is not published; repair requires an immutable published baseline.", undefined, "schedule.not_published")
    );

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/schedules/sched-001"]}>
          <Routes>
            <Route path="/schedules/:scheduleId" element={<ScheduleDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Repair Schedule/i })).toBeInTheDocument();
    });

    const repairBtn = screen.getByRole("button", { name: /Repair Schedule/i });
    fireEvent.click(repairBtn);

    const submitRepairBtn = screen.getByRole("button", { name: /Run CP-SAT Repair/i });
    fireEvent.click(submitRepairBtn);

    await waitFor(() => {
      expect(screen.getByText(/Prerequisite Violation: Schedule repair requires a PUBLISHED schedule baseline/i)).toBeInTheDocument();
    });
  });

  it("renders repaired successor version details in repair modal", async () => {
    vi.spyOn(apiClient, "getSchedule").mockResolvedValue(sampleScheduleDetail);
    const mockRepairResult: ScheduleRepairResponse = {
      job_id: "repair-job-v2",
      baseline_schedule_id: "sched-001",
      candidate_schedule_id: "sched-002-successor",
      candidate_version: 2,
      status: "FEASIBLE",
    };
    vi.spyOn(apiClient, "repairSchedule").mockResolvedValue(mockRepairResult);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/schedules/sched-001"]}>
          <Routes>
            <Route path="/schedules/:scheduleId" element={<ScheduleDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Repair Schedule/i })).toBeInTheDocument();
    });

    const repairBtn = screen.getByRole("button", { name: /Repair Schedule/i });
    fireEvent.click(repairBtn);

    const submitRepairBtn = screen.getByRole("button", { name: /Run CP-SAT Repair/i });
    fireEvent.click(submitRepairBtn);

    await waitFor(() => {
      expect(screen.getByText(/Repair Job Initiated Successfully/i)).toBeInTheDocument();
      expect(screen.getByText(/sched-002-successor/i)).toBeInTheDocument();
      expect(screen.getAllByText(/v2/i)[0]).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /View Repaired Candidate Schedule/i })).toBeInTheDocument();
    });
  });

  it("disables publication and repair when user lacks authority", async () => {
    mockAuthValue.user.roles = ["auditor"];
    mockAuthValue.user.permissions = ["audit:read"];
    vi.spyOn(apiClient, "getSchedule").mockResolvedValue({
      ...sampleScheduleDetail,
      status: "DRAFT",
      published_at: null,
    });

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/schedules/sched-001"]}>
          <Routes>
            <Route path="/schedules/:scheduleId" element={<ScheduleDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      const pubBtn = screen.getByRole("button", { name: /Publish Timetable/i });
      expect(pubBtn).toBeDisabled();
      expect(pubBtn).toHaveAttribute("title", "Publish requires scheduler, operator, or approver role");

      const repairBtn = screen.getByRole("button", { name: /Repair Schedule/i });
      expect(repairBtn).toBeDisabled();
      expect(repairBtn).toHaveAttribute("title", "Repair requires scheduler, operator, or tenant_admin role");
    });
  });
});
