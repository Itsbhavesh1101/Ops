"""Canonical Deterministic OpenAPI Export Utility (Phase 8F).

Model: CANONICAL_DERIVED_OPENAPI
Extracts OpenAPI 3.1.0 specification directly from the canonical FastAPI application
(app.main:app) with all authenticated business domain routers mounted
(E7_MOUNT_DOMAIN_ROUTERS=true).

Deterministic documentation enrichment attaches:
- Formal Security Schemes:
  * BearerAuth: Configured OIDC/JWT identity provider (Google Identity reference configuration)
  * ServiceKeyAuth: Service authentication via persistent key (X-E7-Service-Key)
- Standardized Error Response Components:
  * 400 Bad Request (conflicting credentials, invalid syntax)
  * 401 Unauthorized (missing or invalid credentials)
  * 403 Forbidden (RBAC / tenant isolation failure)
  * 404 Not Found (resource not found)
  * 409 Conflict (idempotency key conflict, concurrency conflict)
  * 413 Payload Too Large (request body limit exceeded)
  * 422 Unprocessable Entity (request schema validation failed)
  * 429 Too Many Requests (capacity.quota_exceeded, Retry-After header)
  * 500 Internal Server Error (unhandled server failure)
- Operation-level security declarations and error bindings (with 429 strictly scoped
  to capacity-governed endpoints).
- Stable, sorted, deterministic JSON formatting for drift testing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Ensure domain routers are mounted and environment is documentation-safe
os.environ["E7_MOUNT_DOMAIN_ROUTERS"] = "true"
os.environ.setdefault("E7_ENVIRONMENT", "production")


def get_raw_openapi_schema() -> dict[str, Any]:
    """Generate the raw un-enriched OpenAPI specification directly from app.main:app."""
    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    app.openapi_schema = None
    raw_schema: dict[str, Any] = app.openapi()
    return json.loads(json.dumps(raw_schema))


def discover_quota_enforced_endpoints() -> set[tuple[str, str]]:
    """Dynamically discover routes enforcing tenant capacity quotas."""
    import inspect

    from fastapi.routing import APIRoute

    from app.main import app

    results: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        try:
            src = inspect.getsource(route.endpoint)
        except Exception:
            continue
        if any(
            term in src
            for term in (
                "check_solve_submission_quota",
                "check_repair_submission_quota",
                "persist_generated_plan",
                "service.start_instance",
            )
        ):
            for method in route.methods:
                if method not in {"HEAD", "OPTIONS"}:
                    results.add((route.path, method.lower()))
    return results


def enrich_openapi_schema(raw_schema: dict[str, Any]) -> dict[str, Any]:
    """Apply deterministic documentation metadata enrichment to raw OpenAPI schema."""
    schema = json.loads(json.dumps(raw_schema))

    # 1. Ensure components exists
    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})

    # 2. Canonical Error Schemas
    if "ApiErrorDetail" not in schemas:
        schemas["ApiErrorDetail"] = {
            "type": "object",
            "title": "ApiErrorDetail",
            "description": (
                "Structural detail for field-level or parameter-level API validation error."
            ),
            "required": ["location", "code", "message"],
            "properties": {
                "location": {
                    "type": "array",
                    "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                    "description": "Path to the field or parameter in the request.",
                },
                "code": {
                    "type": "string",
                    "description": "Machine-readable detail error code.",
                },
                "message": {
                    "type": "string",
                    "description": "Human-readable error explanation.",
                },
            },
        }

    if "ApiErrorBody" not in schemas:
        schemas["ApiErrorBody"] = {
            "type": "object",
            "title": "ApiErrorBody",
            "description": "Canonical E-7 standardized error payload body.",
            "required": ["code", "message", "correlation_id"],
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Machine-readable bounded lowercase namespaced error code.",
                },
                "message": {
                    "type": "string",
                    "description": "Safe public human-readable error message.",
                },
                "correlation_id": {
                    "type": "string",
                    "description": "Request correlation identifier for end-to-end tracing.",
                },
                "details": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/ApiErrorDetail"},
                    "nullable": True,
                    "description": "Optional list of structural field-level validation details.",
                },
            },
        }

    if "ApiErrorResponse" not in schemas:
        schemas["ApiErrorResponse"] = {
            "type": "object",
            "title": "ApiErrorResponse",
            "description": "Canonical top-level API error envelope.",
            "required": ["error"],
            "properties": {
                "error": {"$ref": "#/components/schemas/ApiErrorBody"},
            },
        }

    # 3. Canonical Security Schemes
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "Configured OIDC/JWT identity provider (Google Identity reference configuration). "
            "Provide standard header: 'Authorization: Bearer <OIDC/JWT token>'. "
            "Verified identity maps to tenant context and role bindings."
        ),
    }
    security_schemes["ApiKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-E7-Service-Key",
        "description": (
            "Service-to-service authentication. Provide persistent key header: "
            "'X-E7-Service-Key: e7sk_<credential_id>.<secret>'. Tenant context is "
            "strictly derived from verified credential, not client header."
        ),
    }
    # Backward-compatible alias for existing service-key consumers
    security_schemes["ServiceKeyAuth"] = security_schemes["ApiKeyAuth"]
    security_schemes["GoogleWebhookAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-Goog-Channel-Token",
        "description": (
            "Google Calendar push notification webhook verification. "
            "Validates Google delivery headers ('X-Goog-Channel-Token', 'X-Goog-Channel-ID', "
            "'X-Goog-Resource-ID') against registered durable watch channel state in PostgreSQL. "
            "Exempt from OIDC Bearer and X-E7-Service-Key authentication."
        ),
    }

    # 4. Standard Responses
    responses = components.setdefault("responses", {})
    responses["BadRequestError"] = {
        "description": "Bad Request — Malformed syntax or conflicting authentication credentials.",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiErrorResponse"},
            }
        },
    }
    responses["UnauthorizedError"] = {
        "description": "Unauthorized — Missing, expired, or invalid credentials.",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiErrorResponse"},
            }
        },
    }
    responses["ForbiddenError"] = {
        "description": (
            "Forbidden — Authenticated identity lacks required role or cross-tenant access denied."
        ),
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiErrorResponse"},
            }
        },
    }
    responses["NotFoundError"] = {
        "description": "Not Found — Requested resource does not exist within the tenant boundary.",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiErrorResponse"},
            }
        },
    }
    responses["ConflictError"] = {
        "description": (
            "Conflict — Idempotency payload mismatch or concurrent modification conflict."
        ),
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiErrorResponse"},
            }
        },
    }
    responses["PayloadTooLargeError"] = {
        "description": (
            "Payload Too Large — Request body exceeded configured size limit (10MB default)."
        ),
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiErrorResponse"},
            }
        },
    }
    responses["ValidationError"] = {
        "description": "Unprocessable Entity — Request parameters or payload failed validation.",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiErrorResponse"},
            }
        },
    }
    responses["QuotaExceededError"] = {
        "description": (
            "Too Many Requests — Tenant capacity quota exceeded. Includes Retry-After header."
        ),
        "headers": {
            "Retry-After": {
                "description": "Minimum seconds to wait before retrying request.",
                "schema": {"type": "integer", "example": 30},
            }
        },
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiErrorResponse"},
            }
        },
    }
    responses["InternalServerError"] = {
        "description": (
            "Internal Server Error — Unexpected failure. Safe error envelope returned."
        ),
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiErrorResponse"},
            }
        },
    }

    # 5. Attach Security and Error References
    # Derive endpoints subject to capacity quotas dynamically from route analysis
    quota_governed_endpoints = discover_quota_enforced_endpoints()

    for path, methods in schema.get("paths", {}).items():
        if path == "/api/v1/integrations/calendar/google/webhook":
            # Provider-authenticated webhook: requires Google channel delivery headers
            for method, operation in methods.items():
                if method.lower() != "post":
                    continue
                operation["security"] = [{"GoogleWebhookAuth": []}]
                op_responses = operation.setdefault("responses", {})
                if "400" not in op_responses:
                    op_responses["400"] = {"$ref": "#/components/responses/BadRequestError"}
                if "401" not in op_responses:
                    op_responses["401"] = {"$ref": "#/components/responses/UnauthorizedError"}
                if "403" not in op_responses:
                    op_responses["403"] = {"$ref": "#/components/responses/ForbiddenError"}
                if "404" not in op_responses:
                    op_responses["404"] = {"$ref": "#/components/responses/NotFoundError"}
                if "500" not in op_responses:
                    op_responses["500"] = {"$ref": "#/components/responses/InternalServerError"}
            continue

        if path in {"/", "/api/v1/health", "/api/v1/ready"}:
            # Public unauthenticated routes: zero security requirement
            continue

        for method, operation in methods.items():
            method_lower = method.lower()
            if method_lower not in {"get", "post", "put", "patch", "delete"}:
                continue

            # Add security options: either Bearer or Service Key
            if "security" not in operation:
                operation["security"] = [
                    {"BearerAuth": []},
                    {"ApiKeyAuth": []},
                ]

            op_responses = operation.setdefault("responses", {})
            if "400" not in op_responses:
                op_responses["400"] = {"$ref": "#/components/responses/BadRequestError"}
            if "401" not in op_responses:
                op_responses["401"] = {"$ref": "#/components/responses/UnauthorizedError"}
            if "403" not in op_responses:
                op_responses["403"] = {"$ref": "#/components/responses/ForbiddenError"}
            if "422" not in op_responses:
                op_responses["422"] = {"$ref": "#/components/responses/ValidationError"}
            if "500" not in op_responses:
                op_responses["500"] = {"$ref": "#/components/responses/InternalServerError"}

            # Resource lookups with path parameters return 404
            if "{" in path and "404" not in op_responses:
                op_responses["404"] = {"$ref": "#/components/responses/NotFoundError"}

            # Quota 429 is attached ONLY where tenant capacity quota enforcement actually applies
            if (path, method_lower) in quota_governed_endpoints:
                if "429" not in op_responses:
                    op_responses["429"] = {"$ref": "#/components/responses/QuotaExceededError"}

    return schema


def get_canonical_openapi_schema() -> dict[str, Any]:
    """Generate the canonical derived OpenAPI specification."""
    raw = get_raw_openapi_schema()
    return enrich_openapi_schema(raw)


def sort_json_deterministic(obj: Any) -> Any:
    """Recursively sort dict keys for deterministic serialization."""
    if isinstance(obj, dict):
        return {k: sort_json_deterministic(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [sort_json_deterministic(item) for item in obj]
    return obj


def export_openapi(output_path: Path) -> dict[str, Any]:
    """Export canonically formatted OpenAPI JSON to disk."""
    schema = get_canonical_openapi_schema()
    sorted_schema = sort_json_deterministic(schema)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(sorted_schema, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return sorted_schema


if __name__ == "__main__":
    target = REPO_ROOT / "docs" / "production" / "openapi.json"
    schema = export_openapi(target)
    print(f"Exported canonical OpenAPI to: {target}")
    print(f"Total paths: {len(schema.get('paths', {}))}")
    sec_keys = list(schema.get("components", {}).get("securitySchemes", {}).keys())
    print(f"Security schemes: {sec_keys}")
    resp_keys = list(schema.get("components", {}).get("responses", {}).keys())
    print(f"Standard error responses: {resp_keys}")
