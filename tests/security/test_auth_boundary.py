"""Authentication Boundary Test Suite (Phase 8A Step 3).

Proves real HTTP behavior for:
- no credentials (401)
- invalid OIDC credential (401)
- invalid service key (401)
- valid human principal (200 / processed)
- valid service identity (200 / processed)
- expired credential where supported (401)
- malformed Authorization header (401)
- conflicting auth mechanisms (400)
- standardized error envelope preservation (code, message, correlation_id)
"""

from starlette.testclient import TestClient

from tests.security.conftest import SecurityTestContext


class TestAuthenticationBoundary:
    """Rigorous HTTP boundary tests verifying authentication rejection and acceptance."""

    def test_no_credentials_rejected_401(self, client: TestClient) -> None:
        """Callers providing no credentials must receive 401 Unauthorized."""
        response = client.get("/api/v1/analytics/operations")
        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "auth.credentials_missing"
        assert "Authentication required" in data["error"]["message"]
        assert "correlation_id" in data["error"]

    def test_invalid_oidc_credential_rejected_401(self, client: TestClient) -> None:
        """Callers providing an untrusted/unverifiable bearer token must receive 401."""
        response = client.get(
            "/api/v1/analytics/operations",
            headers={"Authorization": "Bearer invalid_untrusted_token_123"},
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"]["code"] == "auth.token_invalid"
        assert "correlation_id" in data["error"]

    def test_invalid_service_key_rejected_401(self, client: TestClient) -> None:
        """Callers providing a forged or unknown service API key must receive 401."""
        secret = "x" * 45
        forged_key = f"e7sk_unknown_cred.{secret}"
        response = client.get(
            "/api/v1/analytics/operations",
            headers={"X-E7-Service-Key": forged_key},
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"]["code"] == "auth.service_key_invalid"
        assert "correlation_id" in data["error"]

    def test_expired_oidc_credential_rejected_401(self, client: TestClient) -> None:
        """Callers presenting an expired OIDC token must receive 401."""
        response = client.get(
            "/api/v1/analytics/operations",
            headers={"Authorization": "Bearer expired_oidc_token"},
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"]["code"] == "auth.token_invalid"
        assert "Token has expired" in data["error"]["message"]

    def test_malformed_authorization_header_rejected_401(self, client: TestClient) -> None:
        """Malformed Authorization headers (missing Bearer, extra parts, blank) must receive 401."""
        # Missing 'Bearer' keyword
        resp1 = client.get(
            "/api/v1/analytics/operations",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp1.status_code == 401
        assert resp1.json()["error"]["code"] == "auth.invalid_authorization_header"

        # Missing token after Bearer
        resp2 = client.get(
            "/api/v1/analytics/operations",
            headers={"Authorization": "Bearer  "},
        )
        assert resp2.status_code == 401
        assert resp2.json()["error"]["code"] == "auth.invalid_authorization_header"

    def test_conflicting_auth_mechanisms_rejected_400(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Callers providing BOTH Authorization and X-Service-API-Key must be rejected with 400."""
        human_headers = security_context.get_auth_headers("auditor")
        service_headers = security_context.get_auth_headers("service")

        conflicting_headers = {
            **human_headers,
            **service_headers,
        }
        response = client.get(
            "/api/v1/analytics/operations",
            headers=conflicting_headers,
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "auth.conflicting_credentials"
        assert "Conflicting authentication credentials" in data["error"]["message"]

    def test_valid_human_principal_accepted_200(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Valid human principal token succeeds and returns domain payload."""
        headers = security_context.get_auth_headers("auditor")
        response = client.get("/api/v1/analytics/operations", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert "plan_progress" in data
        assert data["tenant_id"] == "tenant-alpha"

    def test_valid_service_identity_accepted_200(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Valid service identity key succeeds and returns domain payload."""
        headers = security_context.get_auth_headers("service")
        # Service principal queries resources availability
        response = client.get(
            "/api/v1/resources/availability?start_at=2026-10-09T00:00:00Z&end_at=2026-10-09T23:59:59Z",
            headers=headers,
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_unregistered_verified_identity_rejected_401(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Verified cryptographic identity not mapped to an active principal receives 401."""
        from app.core.auth.models import VerifiedOidcIdentity

        unmapped_token = "token_unmapped_user"
        security_context.oidc_verifier.register_token(
            unmapped_token,
            VerifiedOidcIdentity(
                issuer="https://accounts.google.com",
                subject="sub_unmapped_user",
                audiences=frozenset({"e7-test-client-id"}),
                email="unmapped@example.com",
            ),
        )
        # Note: Do not register sub_unmapped_user in identity_resolver
        response = client.get(
            "/api/v1/analytics/operations",
            headers={"Authorization": f"Bearer {unmapped_token}"},
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"]["code"] == "auth.principal_unresolved"
