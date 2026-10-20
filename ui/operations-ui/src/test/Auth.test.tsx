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

  it("exposes canonical OIDC configuration structure", () => {
    let capturedAuth: ReturnType<typeof useAuth> | undefined;
    const TestConsumer = () => {
      capturedAuth = useAuth();
      return <div>Provider: {capturedAuth.oidcConfig.provider}</div>;
    };

    render(
      <MemoryRouter>
        <AuthProvider>
          <TestConsumer />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(capturedAuth?.oidcConfig).toBeDefined();
    expect(capturedAuth?.oidcConfig).toHaveProperty("clientId");
    expect(capturedAuth?.oidcConfig).toHaveProperty("issuer");
    expect(capturedAuth?.oidcConfig).toHaveProperty("authorizationEndpoint");
    expect(capturedAuth?.oidcConfig).toHaveProperty("tokenEndpoint");
    expect(capturedAuth?.oidcConfig).toHaveProperty("redirectUri");
    expect(capturedAuth?.oidcConfig).toHaveProperty("isConfigured");
    expect(capturedAuth?.oidcConfig).toHaveProperty("isDemoLocalOidc");
  });

  it("login redirect initiation: generates PKCE parameters and redirects to authorization endpoint", async () => {
    const originalLocation = window.location;
    // Mock window.location
    const mockLocation = {
      href: "",
      origin: "http://127.0.0.1:5173",
    };
    Object.defineProperty(window, "location", {
      value: mockLocation,
      writable: true,
      configurable: true,
    });

    let capturedAuth: ReturnType<typeof useAuth> | undefined;
    const TestConsumer = () => {
      capturedAuth = useAuth();
      return <button onClick={() => capturedAuth?.loginWithPkceRedirect()}>Initiate PKCE</button>;
    };

    render(
      <MemoryRouter>
        <AuthProvider>
          <TestConsumer />
        </AuthProvider>
      </MemoryRouter>
    );

    await act(async () => {
      await capturedAuth?.loginWithPkceRedirect();
    });

    // Verify redirect URL format
    expect(mockLocation.href).toContain("response_type=code");
    expect(mockLocation.href).toContain("code_challenge=");
    expect(mockLocation.href).toContain("code_challenge_method=S256");
    expect(mockLocation.href).toContain("state=");

    // Verify PKCE session stored in sessionStorage
    expect(sessionStorage.getItem("e7_oidc_pkce_state")).toBeTruthy();
    expect(sessionStorage.getItem("e7_oidc_pkce_verifier")).toBeTruthy();

    Object.defineProperty(window, "location", {
      value: originalLocation,
      writable: true,
      configurable: true,
    });
  });

  it("callback/session handling: successfully exchanges code with verifier and calls /auth/me", async () => {
    // Pre-populate sessionStorage with state and verifier as if returning from Keycloak
    const testState = "test-csrf-state-12345678";
    const testVerifier = "test-code-verifier-abcdef0123456789";
    sessionStorage.setItem("e7_oidc_pkce_state", testState);
    sessionStorage.setItem("e7_oidc_pkce_verifier", testVerifier);

    const mockTokenResponse = {
      id_token: "mock.jwt.keycloak.id_token",
      access_token: "mock.jwt.keycloak.access_token",
      token_type: "Bearer",
      expires_in: 300,
    };

    // Mock global fetch for token exchange
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => mockTokenResponse,
    } as Response);

    const mockPlannerUser = {
      principal_id: "usr:planner-001",
      tenant_id: "tenant-pilot-001",
      roles: ["planner"],
      permissions: ["plan:create", "plan:read", "wbs:create"],
      principal_type: "HUMAN" as const,
      authentication_method: "OIDC" as const,
      display_name: "Operations Planner",
      email: "planner@operations.local",
    };

    const getCurrentUserSpy = vi
      .spyOn(apiClient, "getCurrentUser")
      .mockResolvedValueOnce(mockPlannerUser);

    let capturedAuth: ReturnType<typeof useAuth> | undefined;
    const TestConsumer = () => {
      capturedAuth = useAuth();
      return (
        <div>
          <span>Status: {capturedAuth.status}</span>
          <span>User: {capturedAuth.user?.display_name}</span>
        </div>
      );
    };

    render(
      <MemoryRouter>
        <AuthProvider>
          <TestConsumer />
        </AuthProvider>
      </MemoryRouter>
    );

    let resultUser;
    await act(async () => {
      resultUser = await capturedAuth?.handleAuthCallback("auth-code-789", testState);
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(getCurrentUserSpy).toHaveBeenCalledTimes(1);
    expect(resultUser).toEqual(mockPlannerUser);
    expect(capturedAuth?.isAuthenticated).toBe(true);
    expect(capturedAuth?.user?.principal_id).toBe("usr:planner-001");
    expect(capturedAuth?.user?.roles).toContain("planner");

    // Verify PKCE ephemeral session was cleared
    expect(sessionStorage.getItem("e7_oidc_pkce_state")).toBeNull();
    expect(sessionStorage.getItem("e7_oidc_pkce_verifier")).toBeNull();
  });

  it("callback failure: rejects on CSRF state mismatch without exchanging code", async () => {
    sessionStorage.setItem("e7_oidc_pkce_state", "expected-state-value");
    sessionStorage.setItem("e7_oidc_pkce_verifier", "verifier-value");

    const fetchSpy = vi.spyOn(globalThis, "fetch");

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
        await capturedAuth?.handleAuthCallback("auth-code-789", "tampered-state-value");
      } catch (err: unknown) {
        expect((err as Error).message).toMatch(/CSRF state parameter mismatch/);
      }
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(capturedAuth?.status).toBe("AUTH_ERROR");
    expect(capturedAuth?.isAuthenticated).toBe(false);
  });

  it("logout clears in-memory user and resets client state", async () => {
    const mockUser = {
      principal_id: "usr:approver-001",
      tenant_id: "tenant-pilot-001",
      roles: ["approver"],
      permissions: ["plan:approve"],
      principal_type: "HUMAN" as const,
      authentication_method: "OIDC" as const,
      display_name: "Plan Approver",
    };

    vi.spyOn(apiClient, "getCurrentUser").mockResolvedValueOnce(mockUser);
    const setAuthTokenSpy = vi.spyOn(apiClient, "setAuthToken");
    const setActiveTenantIdSpy = vi.spyOn(apiClient, "setActiveTenantId");

    let capturedAuth: ReturnType<typeof useAuth> | undefined;
    const TestConsumer = () => {
      capturedAuth = useAuth();
      return <div>Auth: {capturedAuth.isAuthenticated ? "YES" : "NO"}</div>;
    };

    render(
      <MemoryRouter>
        <AuthProvider>
          <TestConsumer />
        </AuthProvider>
      </MemoryRouter>
    );

    await act(async () => {
      await capturedAuth?.loginWithBearerToken("valid.id_token");
    });
    expect(capturedAuth?.isAuthenticated).toBe(true);

    await act(async () => {
      capturedAuth?.logout();
    });

    expect(capturedAuth?.isAuthenticated).toBe(false);
    expect(capturedAuth?.user).toBeNull();
    expect(setAuthTokenSpy).toHaveBeenCalledWith(null);
    expect(setActiveTenantIdSpy).toHaveBeenCalledWith(null);
  });
});
