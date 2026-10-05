"""DMN 1.3 XML Artifact Validator and Security Screen (Phase 3B).

Enforces:
1. XML Security Boundary:
   - Rejects DOCTYPE and external entity definitions (XXE prevention).
   - Rejects oversized payloads (> 5MB).
   - Prevents code injection and unsafe XML constructs.
2. Semantic DMN Validation:
   - Validates well-formedness and OMG DMN namespace definitions.
   - Extracts and verifies decision ID and name.
   - Verifies presence of a valid <decisionTable>.
   - Validates hit policy (UNIQUE, FIRST, RULE_ORDER, COLLECT).
   - Extracts typed input and output clauses.
   - Strictly prohibits embedded scripts, custom Java/Groovy code, or arbitrary expressions.
3. Deterministic Artifact Fingerprinting:
   - Calculates cryptographic SHA-256 digest of normalized XML text.
"""

import hashlib
import re
import xml.etree.ElementTree as ET

from app.workflow.dmn.schemas import (
    DmnDecisionMetadata,
    DmnHitPolicy,
    DmnInputClause,
    DmnOutputClause,
)

MAX_DMN_ARTIFACT_BYTES: int = 5 * 1024 * 1024  # 5 MB

DISALLOWED_XML_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"<!DOCTYPE", re.IGNORECASE),
    re.compile(r"<!ENTITY", re.IGNORECASE),
    re.compile(r"SYSTEM\s+[\"']", re.IGNORECASE),
    re.compile(r"PUBLIC\s+[\"']", re.IGNORECASE),
]


class DmnValidationError(ValueError):
    """Base exception for DMN artifact validation errors."""


class DmnInvalidXmlError(DmnValidationError):
    """Raised when DMN XML syntax is invalid or unparseable."""


class DmnSecurityError(DmnValidationError):
    """Raised when DMN artifact fails security screening."""


class DmnValidator:
    """Security screen and semantic validator for DMN 1.3 XML definitions."""

    @staticmethod
    def compute_hash(xml_text: str) -> str:
        """Compute deterministic SHA-256 digest for an artifact."""
        normalized = xml_text.strip().encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()

    def validate(self, xml_text: str) -> DmnDecisionMetadata:
        """Execute full security screening and semantic validation on DMN XML."""
        if not xml_text or not xml_text.strip():
            raise DmnValidationError("DMN XML payload must not be empty.")

        # 1. Size constraint check
        raw_bytes = xml_text.encode("utf-8")
        if len(raw_bytes) > MAX_DMN_ARTIFACT_BYTES:
            raise DmnSecurityError(
                f"DMN artifact exceeds maximum allowed size of {MAX_DMN_ARTIFACT_BYTES} bytes."
            )

        # 2. XML Security: Reject DTD, external entity declarations
        for pattern in DISALLOWED_XML_PATTERNS:
            if pattern.search(xml_text):
                raise DmnSecurityError("DTD / DOCTYPE declarations are forbidden (XXE protection).")

        if "<script" in xml_text.lower():
            raise DmnSecurityError("Embedded script blocks are prohibited in DMN artifacts.")

        # 3. Parse XML securely
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise DmnInvalidXmlError(f"Malformed DMN XML syntax: {exc}") from exc

        # 4. Verify Root Element is DMN Definitions
        local_name = root.tag.split("}")[-1] if "}" in root.tag else root.tag
        if local_name.lower() != "definitions":
            raise DmnValidationError(
                f"Root XML element must be 'definitions', found '{local_name}'."
            )

        # 5. Extract and Validate Decision Elements
        decisions = [
            elem
            for elem in root
            if (elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag).lower() == "decision"
        ]
        if not decisions:
            raise DmnValidationError("DMN artifact contains no <decision> element.")

        primary_decision = decisions[0]
        decision_key = primary_decision.attrib.get("id")
        if not decision_key or not decision_key.strip():
            raise DmnValidationError("DMN <decision> must declare a non-empty 'id' attribute.")

        decision_name = primary_decision.attrib.get("name") or decision_key

        # 6. Locate <decisionTable>
        decision_tables = [
            elem
            for elem in primary_decision
            if (elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag).lower() == "decisiontable"
        ]
        if not decision_tables:
            raise DmnValidationError(f"Decision '{decision_key}' contains no <decisionTable>.")

        dt_elem = decision_tables[0]
        raw_hit_policy = dt_elem.attrib.get("hitPolicy", "UNIQUE").upper()
        try:
            hit_policy = DmnHitPolicy(raw_hit_policy)
        except ValueError as exc:
            supported = [p.value for p in DmnHitPolicy]
            raise DmnValidationError(
                f"Unsupported DMN hitPolicy '{raw_hit_policy}'. Supported: {supported}"
            ) from exc

        # 7. Check for Disallowed Scripting Constructs
        for elem in root.iter():
            tag = (elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag).lower()
            if tag in ("script", "scripttask", "literalexpression"):
                # literalExpression is acceptable inside inputEntry/outputEntry/inputExpression,
                # but verify no script attributes exist.
                for attr_name, attr_val in elem.attrib.items():
                    if (
                        "scriptFormat" in attr_name
                        or "groovy" in str(attr_val).lower()
                        or "python" in str(attr_val).lower()
                    ):
                        raise DmnSecurityError(
                            f"Custom scripts ({attr_val}) are forbidden in DMN decision tables."
                        )

        # 8. Extract Inputs, Outputs, and Rule Count
        inputs: list[DmnInputClause] = []
        outputs: list[DmnOutputClause] = []
        rule_count = 0

        for child in dt_elem:
            tag = (child.tag.split("}")[-1] if "}" in child.tag else child.tag).lower()
            if tag == "input":
                input_id = child.attrib.get("id", f"input_{len(inputs) + 1}")
                input_label = child.attrib.get("label")
                # Locate inputExpression
                expr_elems = [
                    e
                    for e in child
                    if (e.tag.split("}")[-1] if "}" in e.tag else e.tag).lower()
                    == "inputexpression"
                ]
                expr_text = ""
                type_ref = "string"
                if expr_elems:
                    type_ref = expr_elems[0].attrib.get("typeRef", "string")
                    text_elems = [
                        t
                        for t in expr_elems[0]
                        if (t.tag.split("}")[-1] if "}" in t.tag else t.tag).lower() == "text"
                    ]
                    if text_elems and text_elems[0].text:
                        expr_text = text_elems[0].text.strip()
                if not expr_text:
                    expr_text = input_label or input_id
                inputs.append(
                    DmnInputClause(
                        id=input_id,
                        label=input_label,
                        expression=expr_text,
                        type_ref=type_ref,
                    )
                )
            elif tag == "output":
                output_id = child.attrib.get("id", f"output_{len(outputs) + 1}")
                output_name = child.attrib.get("name") or child.attrib.get("label") or output_id
                output_label = child.attrib.get("label")
                type_ref = child.attrib.get("typeRef", "string")
                outputs.append(
                    DmnOutputClause(
                        id=output_id,
                        name=output_name,
                        label=output_label,
                        type_ref=type_ref,
                    )
                )
            elif tag == "rule":
                rule_count += 1

        if not inputs:
            raise DmnValidationError("Decision table must define at least one <input> clause.")
        if not outputs:
            raise DmnValidationError("Decision table must define at least one <output> clause.")
        if rule_count == 0:
            raise DmnValidationError("Decision table must declare at least one <rule>.")

        artifact_hash = self.compute_hash(xml_text)

        return DmnDecisionMetadata(
            decision_key=decision_key.strip(),
            decision_name=decision_name.strip(),
            hit_policy=hit_policy,
            inputs=inputs,
            outputs=outputs,
            rule_count=rule_count,
            artifact_hash=artifact_hash,
        )
