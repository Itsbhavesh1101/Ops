"""Pydantic schemas and typed definitions for form-js schemas and submissions (Phase 3B).

Defines:
- Supported form component types (textfield, number, checkbox, select, radio, textarea)
- FormComponent schema matching bpmn-io/form-js structure
- FormDefinitionSchema for versioned form artifacts
- FormSubmissionPayload for human task completion
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FormComponentType(StrEnum):
    """Allowed form-js component types in Phase 3B."""

    TEXTFIELD = "textfield"
    NUMBER = "number"
    CHECKBOX = "checkbox"
    SELECT = "select"
    RADIO = "radio"
    TEXTAREA = "textarea"


class FormComponentValidation(BaseModel):
    """Validation constraints for a form field."""

    model_config = ConfigDict(extra="ignore")

    required: bool = Field(default=False, description="Field must be present and non-empty")
    min: float | None = Field(default=None, description="Minimum numeric value or length")
    max: float | None = Field(default=None, description="Maximum numeric value")
    max_length: int | None = Field(default=2000, description="Maximum allowed string length")


class FormComponentValue(BaseModel):
    """Selectable option for select and radio components."""

    model_config = ConfigDict(extra="ignore")

    label: str = Field(..., description="Human-readable option label")
    value: str = Field(..., description="Machine-readable value submitted in variables")


class FormComponent(BaseModel):
    """Component definition conforming to bpmn-io/form-js JSON structure."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str | None = Field(default=None, description="Optional component element ID")
    key: str = Field(..., description="Canonical variable name produced by this field")
    type: FormComponentType = Field(..., description="Field component type")
    label: str = Field(..., description="Human-readable field label")
    validate_rules: FormComponentValidation = Field(
        default_factory=lambda: FormComponentValidation(),
        alias="validate",
        description="Validation constraints",
    )
    values: list[FormComponentValue] = Field(
        default_factory=list, description="Available options for select or radio types"
    )

    @property
    def validation_rules(self) -> FormComponentValidation:
        return self.validate_rules

    description: str | None = Field(None, description="Optional helper text")


class FormDefinitionSchema(BaseModel):
    """Strict schema for a complete form-js JSON definition."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Unique form identifier")
    schema_version: int = Field(1, alias="schemaVersion", description="form-js schema version")
    type: str = Field("default", description="Form type")
    components: list[FormComponent] = Field(
        default_factory=list, description="List of form field components"
    )


class FormSubmissionPayload(BaseModel):
    """Payload submitted when completing a human task with a form."""

    model_config = ConfigDict(extra="forbid")

    variables: dict[str, Any] = Field(
        ..., description="Key-value mapping of field keys to submitted values"
    )
