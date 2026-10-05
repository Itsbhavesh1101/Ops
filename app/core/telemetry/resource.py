"""OpenTelemetry Resource construction module for the IR Infotech E-7 Operations Engine.

Constructs privacy-safe OpenTelemetry Resource objects containing exclusively
static, low-cardinality platform and service identity attributes.
Strictly forbids tenant, user, credential, event, and job identities in Resource attributes.
"""

import importlib.metadata
from typing import Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes

FORBIDDEN_RESOURCE_ATTRIBUTES = {
    "tenant_id",
    "tenant",
    "organization_id",
    "user_id",
    "email",
    "principal_id",
    "api_key",
    "api_key_id",
    "job_id",
    "event_id",
    "correlation_id",
    "request_payload",
    "payload",
}


def get_application_version() -> str:
    """Retrieve package version from distribution metadata with a safe fallback."""
    try:
        return importlib.metadata.version("operations-engine")
    except Exception:
        # Safe deterministic fallback for uninstalled or editable development environments
        return "0.1.0"


def build_resource(
    service_name: str,
    *,
    service_namespace: str | None = None,
    service_version: str | None = None,
    environment: str = "local",
) -> Resource:
    """Construct a safe OpenTelemetry Resource for process identification.

    Allowed attributes:
    - service.name: Logical service identifier (e.g. e7-api, e7-worker)
    - service.namespace: Platform namespace (e.g. e7)
    - service.version: Application semantic version
    - deployment.environment: Application runtime environment (e.g. local, production)

    All dynamic, tenant, user, credential, or payload fields are strictly forbidden.
    """
    if not service_name or not service_name.strip():
        raise ValueError("service_name is required to build an OpenTelemetry Resource")

    attributes: dict[str, Any] = {
        ResourceAttributes.SERVICE_NAME: service_name.strip(),
        ResourceAttributes.DEPLOYMENT_ENVIRONMENT: environment.strip(),
    }

    if service_namespace and service_namespace.strip():
        attributes[ResourceAttributes.SERVICE_NAMESPACE] = service_namespace.strip()

    resolved_version = service_version or get_application_version()
    if resolved_version:
        attributes[ResourceAttributes.SERVICE_VERSION] = resolved_version.strip()

    # Guard against forbidden attributes
    for key in attributes:
        if key.lower() in FORBIDDEN_RESOURCE_ATTRIBUTES:
            raise ValueError(f"Forbidden attribute in Resource: {key}")

    return Resource.create(attributes)
