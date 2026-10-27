import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuditPage } from "../pages/AuditPage";
import { AuthContextType } from "../context/AuthContext";
import { apiClient } from "../api/client";
import type { AuditEventResponse } from "../api/types";
import { OIDC_CONFIG } from "../api/config";

const mockAuthValue: AuthContextType = {
  status: "AUTHENTICATED",
  user: {
    principal_id: "auditor-01",
    tenant_id: "tenant-engineering",
    roles: ["compliance_auditor"],
    permissions: ["read:audit"],
    principal_type: "HUMAN",
    authentication_method: "OIDC",
    display_name: "Alex Auditor",
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

describe("AuditPage Enterprise Audit Explorer Integration", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const mockAuditRecords: AuditEventResponse[] = [
    {
      audit_event_id: "audit-001",
      schema_version: 1,
      tenant_id: "tenant-engineering",
      actor_principal_id: "ops-user-1",
      actor_principal_type: "HUMAN",
      authentication_method: "OIDC",
      event_type: "TASK_PROGRESS_UPDATED",
      resource_type: "TASK",
      resource_id: "task-101",
      correlation_id: "corr-abc-123",
      recorded_at: "2026-10-25T12:00:00Z",
      outcome: "SUCCESS",
      details: {
        percentage: 100,
        completion_evidence: "Sound check certified by stage technician",
      },
    },
    {
      audit_event_id: "audit-002",
      schema_version: 1,
      tenant_id: "tenant-engineering",
      actor_principal_id: "system-solver",
      actor_principal_type: "SYSTEM",
      authentication_method: "SERVICE_ACCOUNT",
      event_type: "SCHEDULE_OPTIMIZED",
      resource_type: "SCHEDULE",
      resource_id: "sched-501",
      correlation_id: "corr-def-456",
      recorded_at: "2026-10-25T12:15:00Z",
      outcome: "SUCCESS",
      details: {
        hard_violations: 0,
        soft_violations: 2,
      },
    },
  ];

  it("renders loading state while querying immutable audit log", () => {
    vi.spyOn(apiClient, "searchAudit").mockReturnValue(new Promise(() => {}));

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    expect(screen.getByText(/Loading immutable audit trail records\.\.\./i)).toBeInTheDocument();
  });

  it("renders chronological audit records and outcome badges", async () => {
    vi.spyOn(apiClient, "searchAudit").mockResolvedValue(mockAuditRecords);

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("TASK_PROGRESS_UPDATED")).toBeInTheDocument();
    });

    expect(screen.getByText("SCHEDULE_OPTIMIZED")).toBeInTheDocument();
    expect(screen.getAllByText("SUCCESS").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("task-101")).toBeInTheDocument();
    expect(screen.getByTitle("sched-501")).toBeInTheDocument();
    expect(screen.getByText("ops-user-1")).toBeInTheDocument();
  });

  it("applies filtering by event type and correlation ID", async () => {
    const spy = vi.spyOn(apiClient, "searchAudit").mockResolvedValue(mockAuditRecords);

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("TASK_PROGRESS_UPDATED")).toBeInTheDocument();
    });

    const eventTypeInput = screen.getByPlaceholderText(/task\.progress_updated/i);
    fireEvent.change(eventTypeInput, { target: { value: "TASK_PROGRESS_UPDATED" } });

    const applyBtn = screen.getByRole("button", { name: /Apply Filters/i });
    fireEvent.click(applyBtn);

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({
          event_type: "TASK_PROGRESS_UPDATED",
        }),
        "tenant-engineering"
      );
    });
  });

  it("allows inspecting audit event details safely in modal", async () => {
    vi.spyOn(apiClient, "searchAudit").mockResolvedValue(mockAuditRecords);

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("TASK_PROGRESS_UPDATED")).toBeInTheDocument();
    });

    const inspectButtons = screen.getAllByRole("button", { name: /Inspect/i });
    fireEvent.click(inspectButtons[0]);

    await waitFor(() => {
      expect(screen.getByText(/Audit Record: TASK_PROGRESS_UPDATED/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/Sound check certified by stage technician/i)).toBeInTheDocument();
    expect(screen.getByText("corr-abc-123")).toBeInTheDocument();
  });

  it("renders empty state when no audit records match criteria", async () => {
    vi.spyOn(apiClient, "searchAudit").mockResolvedValue([]);

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/No Audit Events Found/i)).toBeInTheDocument();
    });
  });

  it("renders error state when backend audit endpoint rejects request", async () => {
    vi.spyOn(apiClient, "searchAudit").mockRejectedValue(new Error("Audit log access forbidden"));

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Audit log access forbidden")).toBeInTheDocument();
    });

    const retryBtn = screen.getByRole("button", { name: /Retry/i });
    expect(retryBtn).toBeInTheDocument();
  });
});
