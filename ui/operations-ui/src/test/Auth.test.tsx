import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { AuthProvider, useAuth } from "../context/AuthContext";
import { LoginPage } from "../pages/LoginPage";
import { ProtectedRoute } from "../components/layout/ProtectedRoute";
import { apiClient } from "../api/client";

// Mock SystemStatusIndicator in tests
vi.mock("../components/layout/SystemStatusIndicator", () => ({
  SystemStatusIndicator: () => <div data-testid="system-status">System Operational</div>,
}));

describe("Authentication & Session Flow (Security Hardened)", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("truthfully renders 'Authentication Configuration Required' when OIDC client is unconfigured", () => {
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(screen.getByText("IR Infotech Operations Engine")).toBeInTheDocument();
    expect(screen.getByText("Authentication Configuration Required")).toBeInTheDocument();
    expect(
      screen.getByText(/BLOCKED_ON_COMPANY_OIDC_CLIENT_CONFIGURATION/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Machine-to-machine service credentials/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/strictly prohibited from browser login screens/i)
    ).toBeInTheDocument();
  });

  it("proves login page contains NO service key input fields or mock login bypasses", () => {
    const { container } = render(
      <MemoryRouter initialEntries={["/login"]}>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>
    );

    // Assert zero password or text inputs for service keys
    expect(container.querySelectorAll("input")).toHaveLength(0);
    expect(screen.queryByPlaceholderText(/e7sk_/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();

    // Assert zero mock persona buttons
    expect(screen.queryByText(/Mock Login/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Bypass Auth/i)).not.toBeInTheDocument();
  });

  it("strictly rejects service API keys (e7sk_...) from loginWithBearerToken", async () => {
    let capturedAuth: ReturnType<typeof useAuth> | undefined;
    const TestConsumer = () => {
      capturedAuth = useAuth();
      return <div>Status: {capturedAuth.status}</div>;
    };

    render(
      <MemoryRouter>
        <AuthProvider>
          <TestConsumer />
        </AuthProvider>
      </MemoryRouter>
    );

    await act(async () => {
      try {
        await capturedAuth?.loginWithBearerToken("e7sk_cred-pilot.fake_secret_12345");
      } catch (err: unknown) {
        expect((err as Error).message).toMatch(
          /Security Violation: Service API keys.*machine-to-machine credentials/
        );
      }
    });

    await waitFor(() => {
      expect(screen.getByText("Status: AUTH_ERROR")).toBeInTheDocument();
    });
    expect(capturedAuth?.isAuthenticated).toBe(false);

    // Verify zero credentials persisted
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
  });

  it("authenticates human Bearer token in-memory with zero sessionStorage/localStorage persistence", async () => {
    const mockHumanUser = {
      principal_id: "usr:finance-director",
      tenant_id: "tenant-engineering",
      roles: ["approver", "viewer"],
      permissions: ["workflow:approve"],
      principal_type: "HUMAN" as const,
      authentication_method: "OIDC" as const,
      display_name: "Director of Finance",
      email: "finance.director@irinfotech.internal",
    };

    vi.spyOn(apiClient, "getCurrentUser").mockResolvedValueOnce(mockHumanUser);

    let capturedAuth: ReturnType<typeof useAuth> | undefined;
    const AuthObserver = () => {
      capturedAuth = useAuth();
      return null;
    };

    const AuthenticatedView = () => {
      const { user } = useAuth();
      return (
        <div>
          <span>Principal: {user?.principal_id}</span>
          <span>Tenant: {user?.tenant_id}</span>
        </div>
      );
    };

    render(
      <MemoryRouter initialEntries={["/"]}>
        <AuthProvider>
          <AuthObserver />
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <AuthenticatedView />
                </ProtectedRoute>
              }
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    // Initially unauthenticated -> redirects to /login
    await waitFor(() => {
      expect(screen.getByText("Authentication Configuration Required")).toBeInTheDocument();
    });

    // Perform human login via bearer token
    await act(async () => {
      await capturedAuth?.loginWithBearerToken("valid.jwt.id_token_from_google_oidc");
    });

    await waitFor(() => {
      expect(screen.getByText("Principal: usr:finance-director")).toBeInTheDocument();
      expect(screen.getByText("Tenant: tenant-engineering")).toBeInTheDocument();
    });

    // Verify ZERO credentials in storage
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
  });

  it("handles 401 unauthenticated response by clearing human session state", async () => {
    vi.spyOn(apiClient, "getCurrentUser").mockRejectedValueOnce(
      new Error("Invalid session token or signature expired (HTTP 401)")
    );

    let capturedAuth: ReturnType<typeof useAuth> | undefined;
    const TestConsumer = () => {
      capturedAuth = useAuth();
      return <div>Status: {capturedAuth.status}</div>;
    };

    render(
      <MemoryRouter>
        <AuthProvider>
          <TestConsumer />
        </AuthProvider>
      </MemoryRouter>
    );

    await act(async () => {
      try {
        await capturedAuth?.loginWithBearerToken("expired.or.invalid.token");
      } catch (err: unknown) {
        expect((err as Error).message).toMatch(/401/);
      }
    });

    await waitFor(() => {
      expect(screen.getByText("Status: AUTH_ERROR")).toBeInTheDocument();
    });
    expect(capturedAuth?.isAuthenticated).toBe(false);
    expect(capturedAuth?.user).toBeNull();

    // Verify storage remains empty
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
  });
});
