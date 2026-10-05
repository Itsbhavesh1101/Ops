"""API schemas for foundational platform health and readiness endpoints."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness probe response model."""

    status: str = Field(default="ok", description="Process liveness status")
    service: str = Field(default="operations-engine", description="Service identifier")
    environment: str = Field(..., description="Active runtime environment")


class ReadinessResponse(BaseModel):
    """Traffic readiness probe response model."""

    status: str = Field(default="ready", description="Service readiness indicator")
    service: str = Field(default="operations-engine", description="Service identifier")
    environment: str = Field(..., description="Active runtime environment")
