"""Validator and Variable Policy generator for form-js definitions (Phase 3B).

Enforces:
1. Form Definition Security & Syntax:
   - Max size limit (5MB).
   - Strict component whitelist (textfield, number, checkbox, select, radio, textarea).
   - Rejection of custom HTML, script injection, and webhook URLs.
2. Derivation of WorkflowVariablePolicy:
   - Form field keys define allowed variables.
   - Form field types dictate type enforcement.
3. Submission Payload Validation:
   - Validates required fields, type correctness, enum allowed values, and string length bounds.
   - Rejects invalid submissions before the Flowable engine is invoked.
"""

import hashlib
import json
from typing import Any

from pydantic import ValidationError

from app.workflow.forms.schemas import (
    FormComponentType,
    FormDefinitionSchema,
)
from app.workflow.variables import WorkflowVariablePolicy

MAX_FORM_ARTIFACT_BYTES: int = 5 * 1024 * 1024  # 5 MB


class FormValidationError(ValueError):
    """Base exception for form schema or submission validation failures."""


class FormInvalidSubmissionError(FormValidationError):
    """Raised when form submission data fails schema constraint checks."""


class FormSecurityError(FormValidationError):
    """Raised when form definition fails security screening (e.g. script injection)."""


class FormValidator:
    """Security screen, syntax validator, and submission evaluator for form-js artifacts."""

    @staticmethod
    def compute_hash(json_text: str) -> str:
        """Compute deterministic SHA-256 digest of normalized JSON string."""
        normalized = json_text.strip().encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()

    def validate_form_schema(self, raw_input: str | dict[str, Any]) -> FormDefinitionSchema:
        """Validate and parse a form-js definition JSON."""
        json_repr = raw_input if isinstance(raw_input, str) else json.dumps(raw_input)
        if len(json_repr.encode("utf-8")) > MAX_FORM_ARTIFACT_BYTES:
            raise FormSecurityError("Form JSON exceeds maximum allowed size of 5MB.")

        # Fast security screen for embedded scripts or HTML
        lower_text = json_repr.lower()
        if "<script" in lower_text or "javascript:" in lower_text or "onload=" in lower_text:
            raise FormSecurityError("Embedded script pattern detected in form definition.")

        if isinstance(raw_input, str):
            try:
                data = json.loads(raw_input)
            except json.JSONDecodeError as exc:
                raise FormValidationError(f"Invalid JSON syntax: {exc}") from exc
        else:
            data = raw_input

        if isinstance(data, dict):
            valid_component_types = {e.value for e in FormComponentType}
            for comp in data.get("components", []):
                if isinstance(comp, dict) and "type" in comp:
                    comp_type = comp["type"]
                    if comp_type not in valid_component_types:
                        raise FormSecurityError(
                            f"Unwhitelisted component type '{comp_type}' not permitted."
                        )

        try:
            form_def = FormDefinitionSchema.model_validate(data)
        except ValidationError as exc:
            raise FormValidationError(f"Form schema validation error: {exc}") from exc

        if not form_def.components:
            raise FormValidationError("Form definition must include at least one component.")

        # Validate unique component keys
        seen_keys: set[str] = set()
        for comp in form_def.components:
            if comp.key in seen_keys:
                raise FormValidationError(f"Duplicate component key '{comp.key}' in form.")
            seen_keys.add(comp.key)

            # Check select/radio values
            if comp.type in (FormComponentType.SELECT, FormComponentType.RADIO):
                if not comp.values:
                    raise FormValidationError(
                        f"Component '{comp.key}' of type '{comp.type.value}' "
                        "must provide at least one option value."
                    )

        return form_def

    def derive_variable_policy(self, form_def: FormDefinitionSchema) -> WorkflowVariablePolicy:
        """Derive an explicit WorkflowVariablePolicy from the form's defined field keys."""
        allowed_keys = frozenset(comp.key for comp in form_def.components)
        return WorkflowVariablePolicy(allowed_keys=allowed_keys)

    def validate_submission(
        self,
        form_def: FormDefinitionSchema,
        submitted_vars: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate submitted variable values against the form component specifications."""
        validated: dict[str, Any] = {}

        components_by_key = {comp.key: comp for comp in form_def.components}

        # 1. Check for unexpected variables not declared in form
        for key in submitted_vars:
            if key not in components_by_key:
                raise FormInvalidSubmissionError(
                    f"Undeclared variable '{key}' not permitted in form '{form_def.id}'."
                )

        # 2. Check each component requirements
        for comp in form_def.components:
            key = comp.key
            has_value = key in submitted_vars and submitted_vars[key] is not None
            val = submitted_vars.get(key)

            # Check required
            if comp.validate_rules.required:
                if not has_value or (isinstance(val, str) and not val.strip()):
                    raise FormInvalidSubmissionError(f"Missing required field '{key}'.")

            if not has_value:
                continue

            # Check type and constraints
            if comp.type in (FormComponentType.TEXTFIELD, FormComponentType.TEXTAREA):
                if not isinstance(val, str):
                    raise FormInvalidSubmissionError(
                        f"Field '{key}' must be a string, got {type(val).__name__}."
                    )
                max_len = comp.validate_rules.max_length or 2000
                if len(val) > max_len:
                    raise FormInvalidSubmissionError(
                        f"Field '{key}' exceeds maximum length of {max_len} characters."
                    )
                validated[key] = val.strip()

            elif comp.type == FormComponentType.NUMBER:
                if not isinstance(val, (int, float)) or isinstance(val, bool):
                    raise FormInvalidSubmissionError(
                        f"Field '{key}' must be numeric, got {type(val).__name__}."
                    )
                if comp.validate_rules.min is not None and val < comp.validate_rules.min:
                    raise FormInvalidSubmissionError(
                        f"Field '{key}' value {val} is below minimum {comp.validate_rules.min}."
                    )
                if comp.validate_rules.max is not None and val > comp.validate_rules.max:
                    raise FormInvalidSubmissionError(
                        f"Field '{key}' value {val} exceeds maximum allowed "
                        f"{comp.validate_rules.max}."
                    )
                validated[key] = val

            elif comp.type == FormComponentType.CHECKBOX:
                if not isinstance(val, bool):
                    raise FormInvalidSubmissionError(
                        f"Field '{key}' must be a boolean, got {type(val).__name__}."
                    )
                validated[key] = val

            elif comp.type in (FormComponentType.SELECT, FormComponentType.RADIO):
                if not isinstance(val, str):
                    raise FormInvalidSubmissionError(
                        f"Field '{key}' must be a string, got {type(val).__name__}."
                    )
                allowed_vals = {item.value for item in comp.values}
                if val not in allowed_vals:
                    raise FormInvalidSubmissionError(
                        f"Value '{val}' is not an allowed option for field '{key}': "
                        f"{sorted(allowed_vals)}."
                    )
                validated[key] = val

        return validated
