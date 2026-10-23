import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppShell } from "../components/layout/AppShell";
import { AuthContextType } from "../context/AuthContext";
import { OIDC_CONFIG } from "../api/config";

// Mock useAuth
const mockAuthValue: AuthContextType = {
  status: "AUTHENTICATED",
  user: {
    principal_id: "test-admin",
    tenant_id: "tenant-engineering",
    roles: ["admin"],
    permissions: ["read", "write"],
    principal_type: "HUMAN",
    authentication_method: "OIDC",
    display_name: "Test Administrator",
    email: "admin@irinfotech.internal",
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

// Mock SystemStatusIndicator
vi.mock("../components/layout/SystemStatusIndicator", () => ({
  SystemStatusIndicator: () => <div data-testid="system-status">System Operational</div>,
}));

describe("AppShell Layout & Navigation", () => {
  it("renders the application shell with semantic landmarks and user identity", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <AppShell>
          <div data-testid="test-content">Dashboard Content</div>
        </AppShell>
      </MemoryRouter>
    );

    // Skip to content link
    const skipLink = screen.getByRole("link", { name: /skip to main content/i });
    expect(skipLink).toBeInTheDocument();
    expect(skipLink).toHaveAttribute("href", "#main-content");

    // Brand identity
    expect(screen.getAllByText("Operations Engine")[0]).toBeInTheDocument();

    // Tenant and user display
    expect(screen.getByText("tenant-engineering")).toBeInTheDocument();
    expect(screen.getByText("Test Administrator")).toBeInTheDocument();

    // Main content
    expect(screen.getByTestId("test-content")).toBeInTheDocument();
  });

  it("renders all canonical navigation links in sidebar", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <AppShell>
          <div>Content</div>
        </AppShell>
      </MemoryRouter>
    );

    // Check Core
    expect(screen.getByRole("link", { name: /dashboard/i })).toBeInTheDocument();

    // Check Planning
    expect(screen.getByRole("link", { name: /goals/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /plans/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /tasks \/ wbs/i })).toBeInTheDocument();

    // Check Workflows
    expect(screen.getByRole("link", { name: /approval inbox/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /bpmn modeler/i })).toBeInTheDocument();

    // Check Resources
    expect(screen.getByRole("link", { name: /resource catalog/i })).toBeInTheDocument();

    // Check Scheduling
    expect(screen.getByRole("link", { name: /scheduling jobs/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /timetables & schedules/i })).toBeInTheDocument();

    // Check Operations
    expect(screen.getByRole("link", { name: /operations analytics/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /audit trail/i })).toBeInTheDocument();

    // Check System
    expect(screen.getByRole("link", { name: /health & readiness/i })).toBeInTheDocument();
  });

  it("toggles mobile navigation drawer using hamburger button", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <AppShell>
          <div>Content</div>
        </AppShell>
      </MemoryRouter>
    );

    const toggleButton = screen.getByRole("button", { name: /open navigation/i });
    expect(toggleButton).toBeInTheDocument();

    fireEvent.click(toggleButton);

    const closeButton = screen.getByRole("button", { name: /close navigation/i });
    expect(closeButton).toBeInTheDocument();

    fireEvent.click(closeButton);
  });
});
