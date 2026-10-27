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
    roles: ["auditor"],
    permissions: ["audit:read"],
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
    mockAuthValue.user = {
      principal_id: "auditor-01",
      tenant_id: "tenant-engineering",
      roles: ["auditor"],
      permissions: ["audit:read"],
      principal_type: "HUMAN",
      authentication_method: "OIDC",
      display_name: "Alex Auditor",
    };
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

  it("blocks unauthorized user lacking audit:read permission or auditor role", async () => {
    mockAuthValue.user = {
      principal_id: "viewer-01",
      tenant_id: "tenant-engineering",
      roles: ["viewer"],
      permissions: ["plan:read"],
      principal_type: "HUMAN",
      authentication_method: "OIDC",
      display_name: "Viewer Only",
    };

    const spy = vi.spyOn(apiClient, "searchAudit");

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(
        screen.getByText(/Insufficient permissions to inspect audit trail/i)
      ).toBeInTheDocument();
    });

    expect(spy).not.toHaveBeenCalled();
  });

  it("blocks inspection when tenant identity is missing", async () => {
    mockAuthValue.user = {
      principal_id: "auditor-01",
      tenant_id: "",
      roles: ["auditor"],
      permissions: ["audit:read"],
      principal_type: "HUMAN",
      authentication_method: "OIDC",
      display_name: "Auditor Without Tenant",
    };

    const spy = vi.spyOn(apiClient, "searchAudit");

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(
        screen.getByText(/Active authenticated tenant context is required/i)
      ).toBeInTheDocument();
    });

    expect(spy).not.toHaveBeenCalled();
  });

  it("blocks inspection when user session is missing", async () => {
    mockAuthValue.user = null;

    const spy = vi.spyOn(apiClient, "searchAudit");

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(
        screen.getByText(/Authentication session required/i)
      ).toBeInTheDocument();
    });

    expect(spy).not.toHaveBeenCalled();
  });

  it("display-redacts sensitive credentials, tokens, and secrets in event modal while preserving operational data", async () => {
    const sensitiveRecord: AuditEventResponse = {
      audit_event_id: "audit-sec-001",
      schema_version: 1,
      tenant_id: "tenant-engineering",
      actor_principal_id: "admin-01",
      actor_principal_type: "HUMAN",
      authentication_method: "OIDC",
      event_type: "SYSTEM_CONFIG_UPDATED",
      resource_type: "CONFIG",
      resource_id: "cfg-999",
      correlation_id: "corr-sec-999",
      recorded_at: "2026-10-25T13:00:00Z",
      outcome: "SUCCESS",
      details: {
        environment: "production",
        operational_metrics: { throughput: 450, error_rate: 0.001 },
        connection_details: {
          client_secret: "super-secret-passphrase",
          apiKey: "sk-live-99999-secret",
          nested_auth: {
            auth_token: "jwt.token.string",
            password: "mypassword123",
          },
        },
      },
    };

    vi.spyOn(apiClient, "searchAudit").mockResolvedValue([sensitiveRecord]);

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("SYSTEM_CONFIG_UPDATED")).toBeInTheDocument();
    });

    const inspectBtn = screen.getByRole("button", { name: /Inspect/i });
    fireEvent.click(inspectBtn);

    await waitFor(() => {
      expect(screen.getByText(/Immutable Event Payload \(Display Redacted\)/i)).toBeInTheDocument();
    });

    // Verify sensitive values are redacted
    expect(screen.queryByText(/super-secret-passphrase/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/sk-live-99999-secret/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/mypassword123/i)).not.toBeInTheDocument();

    // Verify permitted operational details remain intact
    expect(screen.getByText(/"environment": "production"/i)).toBeInTheDocument();
    expect(screen.getByText(/"throughput": 450/i)).toBeInTheDocument();
  });
});
