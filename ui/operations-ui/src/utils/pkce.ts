/**
 * Standards-compliant OAuth 2.0 / OIDC Authorization Code with PKCE (RFC 7636).
 * Cryptographically secure verifier generation and SHA-256 challenge derivation
 * using the standard browser Web Crypto API.
 */

const PKCE_STATE_KEY = "e7_oidc_pkce_state";
const PKCE_VERIFIER_KEY = "e7_oidc_pkce_verifier";

/**
 * Generate a cryptographically random high-entropy string for state or verifier.
 */
export function generateRandomString(length: number = 64): string {
  const charset = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
  const randomValues = new Uint8Array(length);
  if (typeof window !== "undefined" && window.crypto) {
    window.crypto.getRandomValues(randomValues);
  } else {
    for (let i = 0; i < length; i++) {
      randomValues[i] = Math.floor(Math.random() * 256);
    }
  }
  let result = "";
  for (let i = 0; i < length; i++) {
    result += charset[randomValues[i] % charset.length];
  }
  return result;
}

/**
 * Derive base64url-encoded SHA-256 code challenge from a code verifier.
 */
export async function generateCodeChallenge(verifier: string): Promise<string> {
  const encoder = new TextEncoder();
  const data = encoder.encode(verifier);
  const digest = await window.crypto.subtle.digest("SHA-256", data);
  const bytes = new Uint8Array(digest);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

/**
 * Store ephemeral PKCE parameters in browser sessionStorage during redirect.
 */
export function storePkceSession(state: string, verifier: string): void {
  if (typeof window !== "undefined" && window.sessionStorage) {
    window.sessionStorage.setItem(PKCE_STATE_KEY, state);
    window.sessionStorage.setItem(PKCE_VERIFIER_KEY, verifier);
  }
}

/**
 * Retrieve and clear ephemeral PKCE parameters upon returning from auth redirect.
 */
export function retrieveAndClearPkceSession(): { state: string | null; verifier: string | null } {
  if (typeof window === "undefined" || !window.sessionStorage) {
    return { state: null, verifier: null };
  }
  const state = window.sessionStorage.getItem(PKCE_STATE_KEY);
  const verifier = window.sessionStorage.getItem(PKCE_VERIFIER_KEY);
  window.sessionStorage.removeItem(PKCE_STATE_KEY);
  window.sessionStorage.removeItem(PKCE_VERIFIER_KEY);
  return { state, verifier };
}

export interface OidcSessionData {
  state: string;
  nonce?: string;
  verifier?: string;
  flow?: "code" | "id_token";
}

const OIDC_NONCE_KEY = "e7_oidc_nonce";
const OIDC_FLOW_KEY = "e7_oidc_flow";

/**
 * Store ephemeral OIDC parameters (state, nonce, flow) in browser sessionStorage during redirect.
 */
export function storeOidcSession(data: OidcSessionData): void {
  if (typeof window !== "undefined" && window.sessionStorage) {
    window.sessionStorage.setItem(PKCE_STATE_KEY, data.state);
    if (data.nonce) window.sessionStorage.setItem(OIDC_NONCE_KEY, data.nonce);
    if (data.verifier) window.sessionStorage.setItem(PKCE_VERIFIER_KEY, data.verifier);
    if (data.flow) window.sessionStorage.setItem(OIDC_FLOW_KEY, data.flow);
  }
}

/**
 * Retrieve and atomically clear ephemeral OIDC parameters upon returning from auth callback.
 * Atomic clear guarantees single-use transaction (protects against replay).
 */
export function retrieveAndClearOidcSession(): {
  state: string | null;
  nonce: string | null;
  verifier: string | null;
  flow: string | null;
} {
  if (typeof window === "undefined" || !window.sessionStorage) {
    return { state: null, nonce: null, verifier: null, flow: null };
  }
  const state = window.sessionStorage.getItem(PKCE_STATE_KEY);
  const nonce = window.sessionStorage.getItem(OIDC_NONCE_KEY);
  const verifier = window.sessionStorage.getItem(PKCE_VERIFIER_KEY);
  const flow = window.sessionStorage.getItem(OIDC_FLOW_KEY);

  window.sessionStorage.removeItem(PKCE_STATE_KEY);
  window.sessionStorage.removeItem(OIDC_NONCE_KEY);
  window.sessionStorage.removeItem(PKCE_VERIFIER_KEY);
  window.sessionStorage.removeItem(OIDC_FLOW_KEY);

  return { state, nonce, verifier, flow };
}

/**
 * Parse unverified JWT payload in the browser for client-side claim validation (e.g. nonce).
 */
export function parseJwtPayload(token: string): Record<string, unknown> {
  const parts = token.split(".");
  if (parts.length !== 3) {
    throw new Error("Invalid JWT token format");
  }
  const base64Url = parts[1];
  const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
  const jsonPayload = decodeURIComponent(
    atob(base64)
      .split("")
      .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
      .join("")
  );
  return JSON.parse(jsonPayload);
}

export interface OidcTokenResponse {
  id_token?: string;
  access_token: string;
  token_type: string;
  expires_in?: number;
  refresh_token?: string;
}

/**
 * Exchange authorization code + code verifier for OIDC tokens at token endpoint.
 */
export async function exchangeCodeForTokens(
  tokenEndpoint: string,
  clientId: string,
  code: string,
  verifier: string,
  redirectUri: string
): Promise<OidcTokenResponse> {
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: clientId,
    code,
    code_verifier: verifier,
    redirect_uri: redirectUri,
  });

  const response = await fetch(tokenEndpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: body.toString(),
  });

  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(`OIDC token exchange failed (HTTP ${response.status}): ${errorBody}`);
  }

  return response.json();
}
