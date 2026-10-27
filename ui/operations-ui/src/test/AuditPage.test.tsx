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

  it("does not initiate new audit requests when user types into filter inputs", async () => {
    const spy = vi.spyOn(apiClient, "searchAudit").mockResolvedValue(mockAuditRecords);

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("TASK_PROGRESS_UPDATED")).toBeInTheDocument();
    });

    const initialCallCount = spy.mock.calls.length;
    expect(initialCallCount).toBe(1);

    // Type into draft filter inputs
    const eventTypeInput = screen.getByPlaceholderText(/task\.progress_updated/i);
    const resourceTypeInput = screen.getByPlaceholderText(/task, plan, schedule/i);
    const correlationIdInput = screen.getByPlaceholderText(/Tracking UUID/i);

    fireEvent.change(eventTypeInput, { target: { value: "SCHEDULE_OPTIMIZED" } });
    fireEvent.change(resourceTypeInput, { target: { value: "SCHEDULE" } });
    fireEvent.change(correlationIdInput, { target: { value: "corr-draft-999" } });

    // Verify no new requests were dispatched simply by typing
    expect(spy.mock.calls.length).toBe(initialCallCount);
  });

  it("resets pagination to first page when new filters are applied", async () => {
    // Generate 25 items so Next page button is enabled (limit = 25)
    const twentyFiveRecords: AuditEventResponse[] = Array.from({ length: 25 }, (_, i) => ({
      ...mockAuditRecords[0],
      audit_event_id: `audit-batch-${i}`,
      resource_id: `task-batch-${i}`,
    }));
    const spy = vi.spyOn(apiClient, "searchAudit").mockResolvedValue(twentyFiveRecords);

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Showing offset 0 - 25/i)).toBeInTheDocument();
    });

    // Navigate to next page
    const nextBtn = screen.getByRole("button", { name: /Next/i });
    fireEvent.click(nextBtn);

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({ offset: 25 }),
        "tenant-engineering"
      );
    });

    // Enter draft filter and apply
    const eventTypeInput = screen.getByPlaceholderText(/task\.progress_updated/i);
    fireEvent.change(eventTypeInput, { target: { value: "SCHEDULE_OPTIMIZED" } });

    const applyBtn = screen.getByRole("button", { name: /Apply Filters/i });
    fireEvent.click(applyBtn);

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({
          event_type: "SCHEDULE_OPTIMIZED",
          offset: 0,
        }),
        "tenant-engineering"
      );
    });
  });

  it("preserves applied filters during pagination and refresh", async () => {
    const twentyFiveRecords: AuditEventResponse[] = Array.from({ length: 25 }, (_, i) => ({
      ...mockAuditRecords[0],
      audit_event_id: `audit-item-${i}`,
      event_type: "FILTERED_EVENT",
    }));
    const spy = vi.spyOn(apiClient, "searchAudit").mockResolvedValue(twentyFiveRecords);

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Showing offset 0 - 25/i)).toBeInTheDocument();
    });

    // Apply a filter
    const eventTypeInput = screen.getByPlaceholderText(/task\.progress_updated/i);
    fireEvent.change(eventTypeInput, { target: { value: "FILTERED_EVENT" } });
    const applyBtn = screen.getByRole("button", { name: /Apply Filters/i });
    fireEvent.click(applyBtn);

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({ event_type: "FILTERED_EVENT", offset: 0 }),
        "tenant-engineering"
      );
    });

    // Paginate to next page — applied filter must be preserved
    const nextBtn = screen.getByRole("button", { name: /Next/i });
    fireEvent.click(nextBtn);

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({ event_type: "FILTERED_EVENT", offset: 25 }),
        "tenant-engineering"
      );
    });

    // Refresh — applied filter and current pagination offset must be preserved
    const refreshBtn = screen.getByRole("button", { name: /Refresh/i });
    fireEvent.click(refreshBtn);

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({ event_type: "FILTERED_EVENT", offset: 25 }),
        "tenant-engineering"
      );
    });
  });

  it("clearing filters restores unfiltered query and resets pagination offset", async () => {
    const spy = vi.spyOn(apiClient, "searchAudit").mockResolvedValue(mockAuditRecords);

    render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("TASK_PROGRESS_UPDATED")).toBeInTheDocument();
    });

    // Apply a filter
    const eventTypeInput = screen.getByPlaceholderText(/task\.progress_updated/i);
    fireEvent.change(eventTypeInput, { target: { value: "TASK_PROGRESS_UPDATED" } });
    const applyBtn = screen.getByRole("button", { name: /Apply Filters/i });
    fireEvent.click(applyBtn);

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({ event_type: "TASK_PROGRESS_UPDATED" }),
        "tenant-engineering"
      );
    });

    // Click Clear Filters
    const clearBtn = screen.getByRole("button", { name: /Clear Filters/i });
    fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({
          event_type: undefined,
          resource_type: undefined,
          resource_id: undefined,
          correlation_id: undefined,
          offset: 0,
        }),
        "tenant-engineering"
      );
    });
  });

  it("invalidates and purges previously fetched records when tenant or auth changes", async () => {
    vi.spyOn(apiClient, "searchAudit").mockResolvedValue(mockAuditRecords);

    const { rerender } = render(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("TASK_PROGRESS_UPDATED")).toBeInTheDocument();
    });

    // Switch user to an unauthorized viewer lacking audit:read
    mockAuthValue.user = {
      principal_id: "viewer-02",
      tenant_id: "tenant-engineering",
      roles: ["viewer"],
      permissions: ["plan:read"],
      principal_type: "HUMAN",
      authentication_method: "OIDC",
      display_name: "Viewer Two",
    };

    rerender(
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(
        screen.getByText(/Insufficient permissions to inspect audit trail/i)
      ).toBeInTheDocument();
    });

    // Verify stale records from previous authorized user are NOT displayed
    expect(screen.queryByText("TASK_PROGRESS_UPDATED")).not.toBeInTheDocument();
    expect(screen.queryByText("SCHEDULE_OPTIMIZED")).not.toBeInTheDocument();
  });
});
