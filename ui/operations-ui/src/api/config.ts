/**
 * Canonical Frontend API & Authentication Configuration
 * Supports environment-driven base URL with local development fallback to gateway on port 8088.
 */
export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string) || "http://127.0.0.1:8088";

export const DEFAULT_TENANT_ID: string =
  (import.meta.env.VITE_DEFAULT_TENANT_ID as string) || "tenant-pilot-001";

/**
 * Institutional Google Workspace OIDC Client Configuration.
 * Requires company IT provisioning in Google Cloud Console.
 * Audience expected by backend: e7-pilot-client-id
 */
export const OIDC_CONFIG = {
  clientId: (import.meta.env.VITE_OIDC_CLIENT_ID as string) || "",
  issuer: "https://accounts.google.com",
  audience: "e7-pilot-client-id",
  redirectUri: typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:5173",
  isConfigured: Boolean(import.meta.env.VITE_OIDC_CLIENT_ID),
};
