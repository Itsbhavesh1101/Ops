import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { GoalsPage } from "../pages/GoalsPage";
import { GoalDetailPage } from "../pages/GoalDetailPage";
import { ToastProvider } from "../components/ui/Toast";
import { apiClient } from "../api/client";
import type { GoalResponse, PlanResponse } from "../api/types";

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

describe("Goals Feature Screens (F2)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const sampleGoal: GoalResponse = {
    id: "goal-101",
    tenant_id: "tenant-engineering",
    objective: "Semester Examination 2026 Operations Goal",
    scope: "Campus-wide engineering finals",
    deadline: "2026-12-15T00:00:00Z",
    target_window: { start_at: "2026-12-01T08:00:00Z", end_at: "2026-12-15T18:00:00Z" },
    constraints: ["NO_SUNDAY_EXAMS"],
    resource_scope: ["hall-alpha", "invigilator-lead"],
    context_references: ["reg-2026-v1"],
    priority_target: "HIGH",
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-02T12:00:00Z",
  };

  it("renders goals list successfully with goal metadata", async () => {
    vi.spyOn(apiClient, "listGoals").mockResolvedValue([sampleGoal]);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/goals"]}>
          <Routes>
            <Route path="/goals" element={<GoalsPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    expect(screen.getByText(/Fetching operational goals from engine\.\.\./i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination 2026 Operations Goal")[0]).toBeInTheDocument();
    });

    expect(screen.getAllByText("HIGH")[0]).toBeInTheDocument();
  });

  it("renders empty state when no goals exist", async () => {
    vi.spyOn(apiClient, "listGoals").mockResolvedValue([]);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/goals"]}>
          <Routes>
            <Route path="/goals" element={<GoalsPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByText(/No Operational Goals Found/i)).toBeInTheDocument();
    });
  });

  it("renders error state when goals API returns failure", async () => {
    vi.spyOn(apiClient, "listGoals").mockRejectedValue(new Error("Database connection refused"));

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/goals"]}>
          <Routes>
            <Route path="/goals" element={<GoalsPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByText(/Failed to Load Goals/i)).toBeInTheDocument();
      expect(screen.getByText(/Database connection refused/i)).toBeInTheDocument();
    });
  });

  it("opens create goal modal and submits goal successfully", async () => {
    vi.spyOn(apiClient, "listGoals").mockResolvedValue([]);
    const createSpy = vi.spyOn(apiClient, "createGoal").mockResolvedValue(sampleGoal);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/goals"]}>
          <Routes>
            <Route path="/goals" element={<GoalsPage />} />
            <Route path="/goals/:goalId" element={<div>Goal Detail Target</div>} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getByText(/No Operational Goals Found/i)).toBeInTheDocument();
    });

    const createBtn = screen.getByRole("button", { name: /Define New Goal/i });
    fireEvent.click(createBtn);

    expect(screen.getByText(/Define Operational Goal/i)).toBeInTheDocument();

    const titleInput = screen.getByPlaceholderText(/e\.g\. Conduct Semester 1 Final Examination/i);
    fireEvent.change(titleInput, { target: { value: "Semester Examination 2026 Operations Goal" } });

    const submitBtn = screen.getByRole("button", { name: /Establish Goal/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          objective: "Semester Examination 2026 Operations Goal",
        }),
        "tenant-engineering"
      );
    });
  });

  it("renders goal detail page and triggers plan generation", async () => {
    vi.spyOn(apiClient, "getGoal").mockResolvedValue(sampleGoal);
    vi.spyOn(apiClient, "listPlans").mockResolvedValue([]);
    const mockGeneratedPlan: PlanResponse = {
      id: "plan-555",
      goal_id: "goal-101",
      plan_name: "Generated Examination Plan",
      status: "DRAFT",
      version_number: 1,
      is_baseline: false,
      created_at: "2026-10-02T10:00:00Z",
      updated_at: "2026-10-02T10:00:00Z",
    };
    const generatePlanSpy = vi.spyOn(apiClient, "generatePlan").mockResolvedValue(mockGeneratedPlan);

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={["/goals/goal-101"]}>
          <Routes>
            <Route path="/goals/:goalId" element={<GoalDetailPage />} />
            <Route path="/plans/:planId" element={<div>Plan Detail Target: plan-555</div>} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.getAllByText("Semester Examination 2026 Operations Goal")[0]).toBeInTheDocument();
    });

    const generateBtn = screen.getByRole("button", { name: /Generate Plan/i });
    fireEvent.click(generateBtn);

    await waitFor(() => {
      expect(generatePlanSpy).toHaveBeenCalledWith("goal-101", null, "tenant-engineering");
    });
  });
});
