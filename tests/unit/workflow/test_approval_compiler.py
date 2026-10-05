"""Unit tests for the programmatic Approval BPMN Compiler and pattern templates."""

import pytest

from app.workflow.approval.compiler import ApprovalBpmnCompiler, ApprovalCompilerError
from app.workflow.approval.models import (
    ApprovalPattern,
    ApprovalStepConfig,
    ApprovalWorkflowConfig,
)
from app.workflow.artifacts.validator import BpmnValidator


def test_compiler_single_step_approval():
    compiler = ApprovalBpmnCompiler()
    validator = BpmnValidator()

    config = ApprovalWorkflowConfig(
        process_key="single_approval_proc",
        process_name="Single Approval Process",
        steps=[
            ApprovalStepConfig(
                step_id="step_mgr",
                step_name="Manager Approval",
                pattern=ApprovalPattern.SINGLE,
                assignee="manager_alice",
            )
        ],
    )

    bpmn_xml = compiler.compile(config)
    assert 'id="single_approval_proc"' in bpmn_xml
    assert 'flowable:assignee="manager_alice"' in bpmn_xml

    # Security validation passes
    parsed = validator.validate(bpmn_xml, allow_trusted_expressions=True)
    assert parsed.workflow_key == "single_approval_proc"
    assert parsed.name == "Single Approval Process"


def test_compiler_sequential_approval():
    compiler = ApprovalBpmnCompiler()
    validator = BpmnValidator()

    config = ApprovalWorkflowConfig(
        process_key="sequential_approval_proc",
        process_name="Sequential Approval",
        steps=[
            ApprovalStepConfig(
                step_id="step_lead",
                step_name="Lead Approval",
                pattern=ApprovalPattern.SINGLE,
                candidate_roles=["lead"],
            ),
            ApprovalStepConfig(
                step_id="step_vp",
                step_name="VP Approval",
                pattern=ApprovalPattern.SINGLE,
                candidate_roles=["vp"],
            ),
        ],
    )

    bpmn_xml = compiler.compile(config)
    assert 'flowable:candidateGroups="lead"' in bpmn_xml
    assert 'flowable:candidateGroups="vp"' in bpmn_xml

    parsed = validator.validate(bpmn_xml, allow_trusted_expressions=True)
    assert parsed.workflow_key == "sequential_approval_proc"


def test_compiler_parallel_all_of_approval():
    compiler = ApprovalBpmnCompiler()
    validator = BpmnValidator()

    config = ApprovalWorkflowConfig(
        process_key="parallel_approval_proc",
        process_name="Parallel All-Of Approval",
        steps=[
            ApprovalStepConfig(
                step_id="step_parallel_reviews",
                step_name="Parallel Reviews",
                pattern=ApprovalPattern.PARALLEL_ALL_OF,
                candidate_roles=["legal", "finance", "compliance"],
            )
        ],
    )

    bpmn_xml = compiler.compile(config)
    assert "parallelGateway" in bpmn_xml
    assert 'flowable:candidateGroups="legal"' in bpmn_xml
    assert 'flowable:candidateGroups="finance"' in bpmn_xml
    assert 'flowable:candidateGroups="compliance"' in bpmn_xml

    parsed = validator.validate(bpmn_xml, allow_trusted_expressions=True)
    assert parsed.workflow_key == "parallel_approval_proc"


def test_compiler_quorum_approval():
    compiler = ApprovalBpmnCompiler()
    validator = BpmnValidator()

    config = ApprovalWorkflowConfig(
        process_key="quorum_approval_proc",
        process_name="Quorum 2 of 3 Approval",
        steps=[
            ApprovalStepConfig(
                step_id="step_quorum",
                step_name="Committee Quorum",
                pattern=ApprovalPattern.QUORUM_N_OF_M,
                candidate_roles=["committee"],
                required_quorum=2,
            )
        ],
    )

    bpmn_xml = compiler.compile(config)
    assert "multiInstanceLoopCharacteristics" in bpmn_xml
    assert "${nrOfCompletedInstances &gt;= 2}" in bpmn_xml

    parsed = validator.validate(bpmn_xml, allow_trusted_expressions=True)
    assert parsed.workflow_key == "quorum_approval_proc"


def test_compiler_rejects_empty_steps():
    compiler = ApprovalBpmnCompiler()
    config = ApprovalWorkflowConfig(
        process_key="empty_proc",
        process_name="Empty Process",
        steps=[],
    )
    with pytest.raises(ApprovalCompilerError, match="must contain at least one step"):
        compiler.compile(config)


def test_compiler_rejects_missing_quorum_param():
    compiler = ApprovalBpmnCompiler()
    config = ApprovalWorkflowConfig(
        process_key="bad_quorum_proc",
        process_name="Bad Quorum",
        steps=[
            ApprovalStepConfig(
                step_id="step_q",
                step_name="Quorum",
                pattern=ApprovalPattern.QUORUM_N_OF_M,
                required_quorum=None,  # Missing
            )
        ],
    )
    with pytest.raises(ApprovalCompilerError, match="requires required_quorum >= 1"):
        compiler.compile(config)
