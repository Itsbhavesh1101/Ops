/**
 * Canonical Frontend API & Authentication Configuration
 * Supports environment-driven base URL with local development fallback to gateway on port 8088.
 */
export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string) || "http://127.0.0.1:8088";

export const DEFAULT_TENANT_ID: string =
  (import.meta.env.VITE_DEFAULT_TENANT_ID as string) || "tenant-pilot-001";

const isDemoOidc =
  import.meta.env.VITE_DEMO_LOCAL_OIDC === "true" ||
  import.meta.env.VITE_OIDC_PROVIDER === "keycloak" ||
  Boolean(import.meta.env.VITE_KEYCLOAK_URL) ||
  (import.meta.env.MODE !== "test" &&
    typeof window !== "undefined" &&
    (window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost") &&
    !import.meta.env.VITE_OIDC_CLIENT_ID);

const keycloakBaseUrl =
  (import.meta.env.VITE_KEYCLOAK_URL as string) || "http://127.0.0.1:8180";
const keycloakRealm =
  (import.meta.env.VITE_KEYCLOAK_REALM as string) || "operations-engine-demo";
const keycloakIssuer = `${keycloakBaseUrl}/realms/${keycloakRealm}`;

/**
 * OpenID Connect (OIDC) Client Configuration.
 * Supports:
 * - DEMO: Self-hosted Keycloak with Authorization Code + PKCE
 * - PRODUCTION: Institutional Google Workspace OIDC
 */
export const OIDC_CONFIG = {
  provider: isDemoOidc ? "keycloak" : "google",
  clientId:
    (import.meta.env.VITE_OIDC_CLIENT_ID as string) ||
    (isDemoOidc ? "operations-ui" : ""),
  issuer: isDemoOidc ? keycloakIssuer : "https://accounts.google.com",
  authorizationEndpoint: isDemoOidc
    ? `${keycloakIssuer}/protocol/openid-connect/auth`
    : "https://accounts.google.com/o/oauth2/v2/auth",
  tokenEndpoint: isDemoOidc
    ? `${keycloakIssuer}/protocol/openid-connect/token`
    : "https://oauth2.googleapis.com/token",
  endSessionEndpoint: isDemoOidc
    ? `${keycloakIssuer}/protocol/openid-connect/logout`
    : undefined,
  audience: isDemoOidc
    ? "operations-ui"
    : ((import.meta.env.VITE_OIDC_CLIENT_ID as string) || "e7-pilot-client-id"),
  redirectUri:
    typeof window !== "undefined"
      ? `${window.location.origin}/login`
      : "http://127.0.0.1:5173/login",
  isConfigured: isDemoOidc || Boolean(import.meta.env.VITE_OIDC_CLIENT_ID),
  isDemoLocalOidc: isDemoOidc,
};
