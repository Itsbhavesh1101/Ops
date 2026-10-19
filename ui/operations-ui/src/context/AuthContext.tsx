import React, { createContext, useContext, useState, useCallback } from "react";
import { apiClient } from "../api/client";
import { OIDC_CONFIG } from "../api/config";
import type { AuthenticatedUser } from "../api/types";

export type AuthStatus =
  | "AUTH_INITIALIZING"
  | "UNAUTHENTICATED"
  | "AUTHENTICATED"
  | "AUTH_CONFIGURATION_REQUIRED"
  | "AUTH_ERROR";

export interface AuthContextType {
  status: AuthStatus;
  user: AuthenticatedUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  loginWithBearerToken: (bearerToken: string) => Promise<AuthenticatedUser>;
  logout: () => void;
  clearError: () => void;
  oidcConfig: typeof OIDC_CONFIG;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<AuthenticatedUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>(() =>
    OIDC_CONFIG.isConfigured ? "UNAUTHENTICATED" : "AUTH_CONFIGURATION_REQUIRED"
  );
  const [error, setError] = useState<string | null>(null);

  const clearError = useCallback(() => setError(null), []);

  const logout = useCallback(() => {
    setUser(null);
    apiClient.setAuthToken(null);
    apiClient.setActiveTenantId(null);
    setError(null);
    setStatus(OIDC_CONFIG.isConfigured ? "UNAUTHENTICATED" : "AUTH_CONFIGURATION_REQUIRED");
  }, []);

  const loginWithBearerToken = useCallback(
    async (newToken: string): Promise<AuthenticatedUser> => {
      setError(null);
      const cleanToken = newToken.trim();

      // Guard: strictly reject machine-to-machine service credentials in browser
      if (cleanToken.startsWith("e7sk_")) {
        const securityError =
          "Security Violation: Service API keys (e7sk_...) are machine-to-machine credentials and must never be used for browser user authentication.";
        setError(securityError);
        setStatus("AUTH_ERROR");
        await Promise.resolve();
        throw new Error(securityError);
      }

      setStatus("AUTH_INITIALIZING");
      try {
        apiClient.setAuthToken(cleanToken);
        const userData = await apiClient.getCurrentUser();
        setUser(userData);
        apiClient.setActiveTenantId(userData.tenant_id);
        setStatus("AUTHENTICATED");
        return userData;
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Authentication verification failed";
        setError(msg);
        setStatus("AUTH_ERROR");
        apiClient.setAuthToken(null);
        apiClient.setActiveTenantId(null);
        setUser(null);
        await Promise.resolve();
        throw err;
      }
    },
    []
  );

  return (
    <AuthContext.Provider
      value={{
        status,
        user,
        isAuthenticated: status === "AUTHENTICATED" && !!user,
        isLoading: status === "AUTH_INITIALIZING",
        error,
        loginWithBearerToken,
        logout,
        clearError,
        oidcConfig: OIDC_CONFIG,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
};
