/**
 * Deterministic sensitive data display-redaction utility.
 *
 * Implements client-side presentation hardening for workflow variables and audit payloads.
 * Conforms to backend privacy models (app/workflow/variables.py and app/audit/validation.py).
 * 
 * Preserves backend data minimization and authorization boundaries.
 * Deeply redacts sensitive keys across nested objects, arrays, and unusual casing.
 */

// Denied tokens normalized (lowercase without '_', '-', ' ')
const DENIED_NORMALIZED_TOKENS: readonly string[] = [
  "password",
  "passwd",
  "secret",
  "clientsecret",
  "apikey",
  "token",
  "accesstoken",
  "refreshtoken",
  "idtoken",
  "authorization",
  "cookie",
  "credential",
  "credentials",
  "privatekey",
  "privkey",
  "authtoken",
];

// Sensitive substring checks on raw lowercase keys
const SENSITIVE_SUBSTRINGS: readonly string[] = [
  "password",
  "passwd",
  "secret",
  "token",
  "private_key",
  "api_key",
  "auth_token",
  "access_token",
  "refresh_token",
  "client_secret",
  "credential",
];

export const REDACTED_PLACEHOLDER = "[REDACTED]";

/**
 * Normalizes a key string by lowercasing and stripping separators (_ - whitespace).
 */
export function normalizeKey(key: string): string {
  return key.toLowerCase().replace(/[_\-\s]/g, "");
}

/**
 * Determines whether a dictionary/object key represents sensitive or secret data.
 * Checks normalized form against denied tokens, sensitive substrings, and exact auth keys.
 */
export function isSensitiveKey(key: string): boolean {
  if (typeof key !== "string" || !key.trim()) return false;

  const lower = key.toLowerCase();
  const normalized = normalizeKey(key);

  // Exact normalized match
  if (DENIED_NORMALIZED_TOKENS.includes(normalized)) {
    return true;
  }

  // Token inclusion in normalized key
  for (const token of DENIED_NORMALIZED_TOKENS) {
    if (normalized.includes(token)) {
      return true;
    }
  }

  // Raw lowercase substring checks
  for (const sub of SENSITIVE_SUBSTRINGS) {
    if (lower.includes(sub)) {
      return true;
    }
  }

  // Handle specific auth keys without false-positives on words like 'author' or 'authority'
  if (lower === "auth" || lower.startsWith("auth_") || lower.endsWith("_auth")) {
    return true;
  }

  return false;
}

/**
 * Recursively creates a display-redacted copy of an arbitrary data structure.
 * - Sensitive dictionary keys have their values replaced with `"[REDACTED]"`.
 * - Permitted dictionary keys have their child values recursively redacted.
 * - Arrays are traversed and elements recursively redacted.
 * - Primitives (string, number, boolean, null) are preserved unless their parent key was sensitive.
 * - Does NOT mutate the input structure (preserves immutability of audit records).
 */
export function redactSensitiveForDisplay<T>(data: T): T {
  if (data === null || data === undefined) {
    return data;
  }

  if (Array.isArray(data)) {
    return data.map((item) => redactSensitiveForDisplay(item)) as unknown as T;
  }

  if (typeof data === "object") {
    const result: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(data as Record<string, unknown>)) {
      if (isSensitiveKey(key)) {
        result[key] = REDACTED_PLACEHOLDER;
      } else {
        result[key] = redactSensitiveForDisplay(value);
      }
    }
    return result as T;
  }

  return data;
}
