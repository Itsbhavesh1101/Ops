import { describe, it, expect } from "vitest";
import {
  isSensitiveKey,
  normalizeKey,
  redactSensitiveForDisplay,
  REDACTED_PLACEHOLDER,
} from "../utils/redact";

describe("Sensitive Data Display-Redaction Utility", () => {
  describe("Key Sensitivity Identification", () => {
    it("identifies standard sensitive keys regardless of case or separators", () => {
      expect(isSensitiveKey("password")).toBe(true);
      expect(isSensitiveKey("Password")).toBe(true);
      expect(isSensitiveKey("PASSWORD")).toBe(true);
      expect(isSensitiveKey("pass_word")).toBe(true);
      expect(isSensitiveKey("client_secret")).toBe(true);
      expect(isSensitiveKey("CLIENT-SECRET")).toBe(true);
      expect(isSensitiveKey("apiKey")).toBe(true);
      expect(isSensitiveKey("API_KEY")).toBe(true);
      expect(isSensitiveKey("access_token")).toBe(true);
      expect(isSensitiveKey("refreshToken")).toBe(true);
      expect(isSensitiveKey("private_key")).toBe(true);
      expect(isSensitiveKey("Cookie")).toBe(true);
      expect(isSensitiveKey("Authorization")).toBe(true);
      expect(isSensitiveKey("auth_token")).toBe(true);
    });

    it("normalizes keys by removing separators and lowercasing", () => {
      expect(normalizeKey("Pass_Word-1")).toBe("password1");
      expect(normalizeKey("API__KEY")).toBe("apikey");
      expect(normalizeKey("Client-Secret")).toBe("clientsecret");
    });

    it("does not false-positive on legitimate operational keys", () => {
      expect(isSensitiveKey("author")).toBe(false);
      expect(isSensitiveKey("authority")).toBe(false);
      expect(isSensitiveKey("authentication_method")).toBe(false);
      expect(isSensitiveKey("plan_name")).toBe(false);
      expect(isSensitiveKey("status")).toBe(false);
      expect(isSensitiveKey("duration_ms")).toBe(false);
      expect(isSensitiveKey("decision")).toBe(false);
      expect(isSensitiveKey("task_id")).toBe(false);
    });
  });

  describe("Recursive Display Redaction", () => {
    it("redacts sensitive fields in top-level objects", () => {
      const input = {
        username: "operator_1",
        password: "SuperSecretPassword123!",
        email: "op@irinfotech.com",
      };

      const result = redactSensitiveForDisplay(input);
      expect(result.password).toBe(REDACTED_PLACEHOLDER);
      expect(result.username).toBe("operator_1");
      expect(result.email).toBe("op@irinfotech.com");
      // Immutability check
      expect(input.password).toBe("SuperSecretPassword123!");
    });

    it("redacts deeply nested sensitive keys with unusual capitalization", () => {
      const input = {
        service_config: {
          endpoint: "https://auth.internal.corp/token",
          connection_details: {
            API_KEY: "secret_api_key_value",
            Client_Secret: "very_confidential_secret",
            allowed_ip: "10.0.0.1",
          },
        },
      };

      const result = redactSensitiveForDisplay(input);
      expect(result.service_config.connection_details.API_KEY).toBe(REDACTED_PLACEHOLDER);
      expect(result.service_config.connection_details.Client_Secret).toBe(REDACTED_PLACEHOLDER);
      expect(result.service_config.connection_details.allowed_ip).toBe("10.0.0.1");
      expect(result.service_config.endpoint).toBe("https://auth.internal.corp/token");
    });

    it("redacts objects inside arrays", () => {
      const input = {
        audit_trail: [
          { event: "LOGIN", token: "jwt.token.here", status: "SUCCESS" },
          { event: "EXPORT", file: "report.pdf", status: "COMPLETED" },
        ],
      };

      const result = redactSensitiveForDisplay(input);
      expect(result.audit_trail[0].token).toBe(REDACTED_PLACEHOLDER);
      expect(result.audit_trail[0].event).toBe("LOGIN");
      expect(result.audit_trail[0].status).toBe("SUCCESS");
      expect(result.audit_trail[1].file).toBe("report.pdf");
    });

    it("handles null, boolean, numeric, and array primitives safely", () => {
      const input = {
        is_active: true,
        max_retries: 3,
        score: 0.95,
        optional_note: null,
        tags: ["ops", "infra", "tier-1"],
      };

      const result = redactSensitiveForDisplay(input);
      expect(result).toEqual(input);
    });

    it("redacts entire nested structures when the parent key is sensitive", () => {
      const input = {
        action: "CONNECT",
        auth: {
          type: "oauth2",
          scope: "read:all",
        },
      };

      const result = redactSensitiveForDisplay(input);
      expect(result.action).toBe("CONNECT");
      expect(result.auth).toBe(REDACTED_PLACEHOLDER);
    });
  });
});
