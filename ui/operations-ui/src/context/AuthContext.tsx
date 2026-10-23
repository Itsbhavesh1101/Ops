import React, { createContext, useContext, useState, useCallback } from "react";
import { apiClient } from "../api/client";
import { OIDC_CONFIG } from "../api/config";
import type { AuthenticatedUser } from "../api/types";
import {
  generateCodeChallenge,
  generateRandomString,
  storePkceSession,
  retrieveAndClearPkceSession,
  storeOidcSession,
  retrieveAndClearOidcSession,
  parseJwtPayload,
  exchangeCodeForTokens,
} from "../utils/pkce";

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
  loginWithPkceRedirect: () => Promise<void>;
  loginWithGoogleRedirect: () => Promise<void>;
  handleAuthCallback: (code: string, state: string) => Promise<AuthenticatedUser>;
  handleIdTokenCallback: (hash: string) => Promise<AuthenticatedUser>;
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
  const [idTokenHint, setIdTokenHint] = useState<string | null>(null);

  const clearError = useCallback(() => setError(null), []);

  const logout = useCallback(() => {
    setUser(null);
    apiClient.setAuthToken(null);
    apiClient.setActiveTenantId(null);
    setError(null);
    setStatus(OIDC_CONFIG.isConfigured ? "UNAUTHENTICATED" : "AUTH_CONFIGURATION_REQUIRED");

    // Optional: terminate Keycloak session via standard OIDC end_session_endpoint
    if (OIDC_CONFIG.endSessionEndpoint && typeof window !== "undefined") {
      const params = new URLSearchParams({
        client_id: OIDC_CONFIG.clientId,
        post_logout_redirect_uri: OIDC_CONFIG.redirectUri,
      });
      if (idTokenHint) {
        params.set("id_token_hint", idTokenHint);
      }
      setIdTokenHint(null);
      window.location.href = `${OIDC_CONFIG.endSessionEndpoint}?${params.toString()}`;
    }
  }, [idTokenHint]);

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

  const loginWithPkceRedirect = useCallback(async (): Promise<void> => {
    setError(null);
    try {
      const state = generateRandomString(32);
      const codeVerifier = generateRandomString(64);
      const codeChallenge = await generateCodeChallenge(codeVerifier);

      storePkceSession(state, codeVerifier);

      const params = new URLSearchParams({
        response_type: "code",
        client_id: OIDC_CONFIG.clientId,
        redirect_uri: OIDC_CONFIG.redirectUri,
        scope: "openid profile email",
        state,
        code_challenge: codeChallenge,
        code_challenge_method: "S256",
      });

      window.location.href = `${OIDC_CONFIG.authorizationEndpoint}?${params.toString()}`;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to initiate OIDC PKCE login";
      setError(msg);
      setStatus("AUTH_ERROR");
    }
  }, []);

  const handleAuthCallback = useCallback(
    async (code: string, returnedState: string): Promise<AuthenticatedUser> => {
      setError(null);
      setStatus("AUTH_INITIALIZING");

      const { state: storedState, verifier } = retrieveAndClearPkceSession();

      if (!storedState || storedState !== returnedState) {
        const stateErr = "OIDC Authentication Error: CSRF state parameter mismatch.";
        setError(stateErr);
        setStatus("AUTH_ERROR");
        throw new Error(stateErr);
      }

      if (!verifier) {
        const verifierErr = "OIDC Authentication Error: Missing PKCE code verifier in session.";
        setError(verifierErr);
        setStatus("AUTH_ERROR");
        throw new Error(verifierErr);
      }

      try {
        const tokenResp = await exchangeCodeForTokens(
          OIDC_CONFIG.tokenEndpoint,
          OIDC_CONFIG.clientId,
          code,
          verifier,
          OIDC_CONFIG.redirectUri
        );

        const bearerToken = tokenResp.id_token || tokenResp.access_token;
        if (!bearerToken) {
          throw new Error("Token endpoint returned neither id_token nor access_token");
        }

        if (tokenResp.id_token) {
          setIdTokenHint(tokenResp.id_token);
        }

        return await loginWithBearerToken(bearerToken);
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Failed to complete token exchange";
        setError(msg);
        setStatus("AUTH_ERROR");
        throw err;
      }
    },
    [loginWithBearerToken]
  );

  const loginWithGoogleRedirect = useCallback(async (): Promise<void> => {
    setError(null);
    try {
      const state = generateRandomString(32);
      const nonce = generateRandomString(32);

      storeOidcSession({
        state,
        nonce,
        flow: "id_token",
      });

      const params = new URLSearchParams({
        response_type: "id_token",
        client_id: OIDC_CONFIG.clientId,
        redirect_uri: OIDC_CONFIG.redirectUri,
        scope: "openid profile email",
        state,
        nonce,
      });

      window.location.href = `${OIDC_CONFIG.authorizationEndpoint}?${params.toString()}`;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to initiate Google OIDC login";
      setError(msg);
      setStatus("AUTH_ERROR");
    }
  }, []);

  const handleIdTokenCallback = useCallback(
    async (rawHash: string): Promise<AuthenticatedUser> => {
      setError(null);
      setStatus("AUTH_INITIALIZING");

      // 1. Immediately scrub the URL fragment to prevent token leakage in history/referrers
      if (typeof window !== "undefined" && window.history && window.history.replaceState) {
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      }

      const cleanHash = rawHash.startsWith("#") ? rawHash.substring(1) : rawHash;
      const params = new URLSearchParams(cleanHash);
      const returnedError = params.get("error_description") || params.get("error");
      if (returnedError) {
        const err = `OIDC Provider Error: ${returnedError}`;
        setError(err);
        setStatus("AUTH_ERROR");
        throw new Error(err);
      }

      const idToken = params.get("id_token");
      const returnedState = params.get("state");

      // 2. Retrieve and atomically clear ephemeral session (single-use transaction protects against replay)
      const session = retrieveAndClearOidcSession();

      // 3. Reject unsolicited ID tokens (no active login session in browser)
      if (!session.state || !session.nonce) {
        const unsolicitedErr =
          "OIDC Authentication Error: Unsolicited ID token rejected (no active login transaction).";
        setError(unsolicitedErr);
        setStatus("AUTH_ERROR");
        throw new Error(unsolicitedErr);
      }

      // 4. Reject state mismatch (CSRF protection)
      if (!returnedState || returnedState !== session.state) {
        const stateErr = "OIDC Authentication Error: CSRF state parameter mismatch.";
        setError(stateErr);
        setStatus("AUTH_ERROR");
        throw new Error(stateErr);
      }

      // 5. Ensure id_token is present
      if (!idToken) {
        const missingTokenErr = "OIDC Authentication Error: Missing id_token in callback fragment.";
        setError(missingTokenErr);
        setStatus("AUTH_ERROR");
        throw new Error(missingTokenErr);
      }

      // 6. Validate nonce claim inside token payload matches stored nonce
      try {
        const payload = parseJwtPayload(idToken);
        const tokenNonce = payload.nonce;
        if (!tokenNonce || tokenNonce !== session.nonce) {
          const nonceErr = "OIDC Authentication Error: Token nonce mismatch or missing.";
          setError(nonceErr);
          setStatus("AUTH_ERROR");
          throw new Error(nonceErr);
        }
      } catch (parseErr: unknown) {
        const parseMsg =
          parseErr instanceof Error
            ? `OIDC Authentication Error: ${parseErr.message}`
            : "OIDC Authentication Error: Malformed token payload.";
        setError(parseMsg);
        setStatus("AUTH_ERROR");
        throw new Error(parseMsg);
      }

      // 7. Verify token cryptographically and resolve principal on backend
      return await loginWithBearerToken(idToken);
    },
    [loginWithBearerToken]
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
        loginWithPkceRedirect,
        loginWithGoogleRedirect,
        handleAuthCallback,
        handleIdTokenCallback,
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
