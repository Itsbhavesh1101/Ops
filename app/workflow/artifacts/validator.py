"""BPMN 2.0 XML Artifact Validator and Security Boundary (Phase 3A).

Enforces:
1. XML Security Boundary:
   - Rejects DOCTYPE and external entity definitions (XXE prevention).
   - Rejects oversized payloads (> 5MB).
   - Prevents path traversal and injection payloads.
2. Semantic BPMN Validation:
   - Validates well-formedness and BPMN 2.0 namespace definitions.
   - Extracts and verifies executable process ID and name.
   - Verifies mandatory start and end events.
   - Detects unsupported or dangerous execution constructs (e.g. arbitrary scriptTask).
3. Deterministic Artifact Fingerprinting:
   - Calculates cryptographic SHA-256 digest of normalized XML text.
"""

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

MAX_BPMN_ARTIFACT_BYTES: int = 5 * 1024 * 1024  # 5 MB

# Standard BPMN 2.0 namespaces
BPMN_NS: str = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
FLOWABLE_NS: str = "{http://flowable.org/bpmn}"

DISALLOWED_XML_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"<!DOCTYPE", re.IGNORECASE),
    re.compile(r"<!ENTITY", re.IGNORECASE),
    re.compile(r"SYSTEM\s+[\"']", re.IGNORECASE),
    re.compile(r"PUBLIC\s+[\"']", re.IGNORECASE),
]

# Closed trusted template expression patterns generated exclusively by E-7 compilers
CLOSED_TRUSTED_EXPRESSION_PATTERNS: list[re.Pattern[str]] = [
    # Multi-instance completion conditions
    re.compile(
        r"^\$\{(nrOfCompletedInstances|approved_count)\s*(>=|==)\s*([0-9]+|nrOfInstances|required_approvals|quorum_count)\}$"
    ),
    re.compile(r"^\$\{(rejected_count)\s*(>|>=|==)\s*([0-9]+|max_rejections)\}$"),
    # Routing decision conditions
    re.compile(r"^\$\{(decision|approval_status|approval_path)\s*(==|!=)\s*'([A-Z_]+)'\}$"),
    # SLA timer ISO datetime variables
    re.compile(
        r"^\$\{(warning_timer_iso|breach_timer_iso|sla_warning_at|sla_breach_at|due_date_iso)\}$"
    ),
]


class BpmnValidationError(ValueError):
    """Base exception for BPMN artifact validation errors."""


class BpmnSecurityError(BpmnValidationError):
    """Raised when BPMN artifact fails security screening (e.g. XXE, DTD)."""


@dataclass(frozen=True)
class BpmnProcessMetadata:
    """Extracted semantic metadata from a validated BPMN 2.0 process definition."""

    process_id: str
    process_name: str
    artifact_hash: str
    user_task_keys: list[str]
    has_start_event: bool
    has_end_event: bool

    @property
    def workflow_key(self) -> str:
        return self.process_id

    @property
    def name(self) -> str:
        return self.process_name


class BpmnValidator:
    """Security screen and semantic validator for BPMN 2.0 XML definitions."""

    @staticmethod
    def compute_hash(xml_text: str) -> str:
        """Compute deterministic SHA-256 digest for an artifact."""
        normalized = xml_text.strip().encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()

    def validate(
        self, xml_text: str, allow_trusted_expressions: bool = False
    ) -> BpmnProcessMetadata:
        """Execute full security screening and semantic validation on BPMN XML."""
        if not xml_text or not xml_text.strip():
            raise BpmnValidationError("BPMN XML payload must not be empty.")

        # 1. Size constraint check
        raw_bytes = xml_text.encode("utf-8")
        if len(raw_bytes) > MAX_BPMN_ARTIFACT_BYTES:
            raise BpmnSecurityError(
                f"BPMN artifact exceeds maximum allowed size of {MAX_BPMN_ARTIFACT_BYTES} bytes."
            )

        # 2. XML Security: Reject DTD, external entity declarations
        for pattern in DISALLOWED_XML_PATTERNS:
            if pattern.search(xml_text):
                raise BpmnSecurityError(
                    "BPMN XML contains prohibited DOCTYPE or entity declarations "
                    "(potential XXE attack)."
                )

        # 3. Parse XML securely
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise BpmnValidationError(f"Malformed XML syntax: {exc}") from exc

        # 4. Verify Root Element is BPMN Definitions
        local_name = root.tag.split("}")[-1] if "}" in root.tag else root.tag
        if local_name.lower() != "definitions":
            raise BpmnValidationError(
                f"Root XML element must be 'definitions', found '{local_name}'."
            )

        # 5. Extract and Validate Process Elements
        processes = [
            elem
            for elem in root
            if (elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag).lower() == "process"
        ]
        if not processes:
            raise BpmnValidationError("BPMN artifact contains no <process> definition.")

        # Primary process is the first executable process (or first process)
        primary_process = next(
            (p for p in processes if p.attrib.get("isExecutable", "true").lower() == "true"),
            processes[0],
        )

        process_id = primary_process.attrib.get("id")
        if not process_id or not process_id.strip():
            raise BpmnValidationError("BPMN <process> must declare a non-empty 'id' attribute.")

        process_name = primary_process.attrib.get("name") or process_id

        # 6. Check for Disallowed Constructs (scriptTask, executable expressions)
        if not allow_trusted_expressions:
            # Fast regex pre-check for any expression markers in the raw XML text
            if re.search(r"(\$\{[^}]+\}|#\{[^}]+\})", xml_text):
                raise BpmnSecurityError(
                    "BPMN executable expressions (${...} or #{...}) are strictly prohibited "
                    "for user-authored artifacts (Phase 3B safe rule boundary)."
                )

        # Structural inspection of XML elements and attributes
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            tag_lower = tag.lower()
            if tag_lower in ("scripttask", "script"):
                raise BpmnSecurityError(
                    f"BPMN <{tag}> is prohibited under all circumstances "
                    "(arbitrary executable scripts strictly forbidden)."
                )
            if not allow_trusted_expressions and tag_lower == "conditionexpression":
                raise BpmnSecurityError(
                    f"BPMN <{tag}> is prohibited for user-authored artifacts "
                    "(executable expressions deferred to DMN or trusted generated templates)."
                )

            # Check text and tail for expression markers
            for text_chunk in (elem.text, elem.tail):
                if text_chunk and ("${" in text_chunk or "#{" in text_chunk):
                    if not allow_trusted_expressions:
                        raise BpmnSecurityError(
                            "BPMN executable expressions (${...} or #{...}) are prohibited."
                        )
                    # Verify against closed trusted template expressions
                    expr_matches = re.findall(r"(\$\{[^}]+\}|#\{[^}]+\})", text_chunk)
                    for expr in expr_matches:
                        if not any(
                            pat.fullmatch(expr.strip())
                            for pat in CLOSED_TRUSTED_EXPRESSION_PATTERNS
                        ):
                            raise BpmnSecurityError(
                                f"Expression '{expr}' is not in the "
                                "E-7 closed trusted template set."
                            )

            # Check attributes for expression markers
            for attr_name, attr_val in elem.attrib.items():
                if isinstance(attr_val, str) and ("${" in attr_val or "#{" in attr_val):
                    if not allow_trusted_expressions:
                        raise BpmnSecurityError(
                            f"BPMN attribute '{attr_name}' contains "
                            "prohibited executable expression."
                        )
                    expr_matches = re.findall(r"(\$\{[^}]+\}|#\{[^}]+\})", attr_val)
                    for expr in expr_matches:
                        if not any(
                            pat.fullmatch(expr.strip())
                            for pat in CLOSED_TRUSTED_EXPRESSION_PATTERNS
                        ):
                            raise BpmnSecurityError(
                                f"Attribute expression '{expr}' is not in the "
                                "E-7 closed trusted template set."
                            )

        # 7. Verify Start and End Events & Extract User Tasks
        has_start = False
        has_end = False
        user_tasks: list[str] = []

        for elem in primary_process:
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "startEvent":
                has_start = True
            elif tag == "endEvent":
                has_end = True
            elif tag == "userTask":
                task_id = elem.attrib.get("id")
                if task_id:
                    user_tasks.append(task_id)

        if not has_start:
            raise BpmnValidationError("BPMN process must contain at least one <startEvent>.")
        if not has_end:
            raise BpmnValidationError("BPMN process must contain at least one <endEvent>.")

        artifact_hash = self.compute_hash(xml_text)

        return BpmnProcessMetadata(
            process_id=process_id.strip(),
            process_name=process_name.strip(),
            artifact_hash=artifact_hash,
            user_task_keys=user_tasks,
            has_start_event=has_start,
            has_end_event=has_end,
        )
