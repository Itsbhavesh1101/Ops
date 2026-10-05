"""Unit tests for BPMN artifact validation, security, and hashing.

Covers Stage E:
- Well-formed BPMN XML validation
- Process ID and executable checks
- XXE / DTD expansion rejection
- Forbidden scriptTask rejection
- Start / End event structural checks
- SHA-256 artifact hashing
"""

import pytest

from app.workflow.artifacts.validator import (
    BpmnSecurityError,
    BpmnValidationError,
    BpmnValidator,
)

VALID_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             targetNamespace="http://e7.irinfotech.com/workflows">
  <process id="sample_approval" name="Sample Approval" isExecutable="true">
    <startEvent id="start" />
    <userTask id="task1" name="Approve Request" />
    <sequenceFlow id="f1" sourceRef="start" targetRef="task1" />
    <endEvent id="end" />
    <sequenceFlow id="f2" sourceRef="task1" targetRef="end" />
  </process>
</definitions>
"""

XXE_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE definitions [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">
  <process id="evil_process" isExecutable="true">
    <startEvent id="start" />
    <endEvent id="end" />
  </process>
</definitions>
"""

SCRIPT_TASK_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             targetNamespace="http://e7.irinfotech.com/workflows">
  <process id="script_process" name="Script Process" isExecutable="true">
    <startEvent id="start" />
    <scriptTask id="unsafe_script" scriptFormat="groovy">
      <script>System.exit(1);</script>
    </scriptTask>
    <sequenceFlow id="f1" sourceRef="start" targetRef="unsafe_script" />
    <endEvent id="end" />
  </process>
</definitions>
"""

MISSING_END_EVENT_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">
  <process id="infinite_process" isExecutable="true">
    <startEvent id="start" />
    <userTask id="task1" />
  </process>
</definitions>
"""

MISSING_START_EVENT_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">
  <process id="no_start_process" isExecutable="true">
    <userTask id="task1" />
    <endEvent id="end" />
  </process>
</definitions>
"""


def test_valid_bpmn_validation_and_hash():
    validator = BpmnValidator()
    result = validator.validate(VALID_BPMN)
    assert result.process_id == "sample_approval"
    assert result.process_name == "Sample Approval"
    assert len(result.artifact_hash) == 64  # SHA-256 hex
    assert result.has_start_event is True
    assert result.has_end_event is True
    assert "task1" in result.user_task_keys


def test_reject_xxe_entity_expansion():
    validator = BpmnValidator()
    with pytest.raises(BpmnSecurityError) as exc_info:
        validator.validate(XXE_BPMN)
    assert "prohibited DOCTYPE or entity" in str(exc_info.value)


def test_reject_forbidden_script_task():
    validator = BpmnValidator()
    with pytest.raises(BpmnSecurityError) as exc_info:
        validator.validate(SCRIPT_TASK_BPMN)
    assert "scriptTask" in str(exc_info.value)


def test_reject_missing_end_event():
    validator = BpmnValidator()
    with pytest.raises(BpmnValidationError) as exc_info:
        validator.validate(MISSING_END_EVENT_BPMN)
    assert "at least one <endEvent>" in str(exc_info.value)


def test_reject_missing_start_event():
    validator = BpmnValidator()
    with pytest.raises(BpmnValidationError) as exc_info:
        validator.validate(MISSING_START_EVENT_BPMN)
    assert "at least one <startEvent>" in str(exc_info.value)


def test_reject_empty_payload():
    validator = BpmnValidator()
    with pytest.raises(BpmnValidationError) as exc_info:
        validator.validate("")
    assert "must not be empty" in str(exc_info.value)


def test_reject_malformed_xml():
    validator = BpmnValidator()
    with pytest.raises(BpmnValidationError) as exc_info:
        validator.validate("<not valid xml")
    assert "Malformed XML" in str(exc_info.value)


def test_reject_executable_dollar_expression():
    """Verify rejection of ${...} executable expression in BPMN sequence flow / gateway."""
    bpmn_with_expr = """<?xml version="1.0" encoding="UTF-8"?>
    <definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
                 targetNamespace="http://e7.irinfotech.com/workflows">
      <process id="expr_proc" isExecutable="true">
        <startEvent id="start" />
        <sequenceFlow id="f1" sourceRef="start" targetRef="end">
          <conditionExpression>${budget >= 10000}</conditionExpression>
        </sequenceFlow>
        <endEvent id="end" />
      </process>
    </definitions>"""
    validator = BpmnValidator()
    with pytest.raises(BpmnSecurityError) as exc_info:
        validator.validate(bpmn_with_expr)
    err_msg = str(exc_info.value)
    assert "executable expressions" in err_msg or "conditionExpression" in err_msg


def test_reject_executable_hash_expression():
    """Verify rejection of #{...} executable expression in BPMN sequence flow."""
    bpmn_with_hash = """<?xml version="1.0" encoding="UTF-8"?>
    <definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
                 targetNamespace="http://e7.irinfotech.com/workflows">
      <process id="hash_proc" isExecutable="true">
        <startEvent id="start" />
        <sequenceFlow id="f1" sourceRef="start" targetRef="end">
          <conditionExpression>#{amount > 500}</conditionExpression>
        </sequenceFlow>
        <endEvent id="end" />
      </process>
    </definitions>"""
    validator = BpmnValidator()
    with pytest.raises(BpmnSecurityError) as exc_info:
        validator.validate(bpmn_with_hash)
    err_msg = str(exc_info.value)
    assert "executable expressions" in err_msg or "conditionExpression" in err_msg


def test_reject_expression_in_attribute():
    """Verify rejection of ${...} expressions placed inside element attributes."""
    bpmn_attr_expr = """<?xml version="1.0" encoding="UTF-8"?>
    <definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
                 xmlns:flowable="http://flowable.org/bpmn"
                 targetNamespace="http://e7.irinfotech.com/workflows">
      <process id="attr_proc" isExecutable="true">
        <startEvent id="start" />
        <userTask id="t1" flowable:assignee="${initiator}" />
        <sequenceFlow id="f1" sourceRef="start" targetRef="t1" />
        <endEvent id="end" />
      </process>
    </definitions>"""
    validator = BpmnValidator()
    with pytest.raises(BpmnSecurityError) as exc_info:
        validator.validate(bpmn_attr_expr)
    assert "executable expressions" in str(exc_info.value) or "prohibited" in str(exc_info.value)


def test_accept_safe_sequential_fixture():
    """Verify the safe sequential two-step approval fixture passes validation."""
    from pathlib import Path

    fixture_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "workflows"
        / "bpmn"
        / "two_step_approval.bpmn20.xml"
    )
    assert fixture_path.exists()
    xml_content = fixture_path.read_text(encoding="utf-8")
    validator = BpmnValidator()
    result = validator.validate(xml_content)
    assert result.process_id == "two_step_approval_process"
    assert "chair_review" in result.user_task_keys
    assert "dean_review" in result.user_task_keys
    assert result.has_start_event is True
    assert result.has_end_event is True
