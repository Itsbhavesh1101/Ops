"""E-7 Controlled BPMN 2.0 Compiler for Advanced Approval Models (Phase 3B).

Compiles typed ApprovalWorkflowConfig instances into standards-compliant BPMN 2.0 XML.
Uses strictly CLOSED trusted expressions for multi-instance completion conditions,
ensuring security screening in BpmnValidator passes without exposing raw expression
authoring to end users.
"""

from xml.sax.saxutils import escape

from app.workflow.approval.models import (
    ApprovalPattern,
    ApprovalStepConfig,
    ApprovalType,
    ApprovalWorkflowConfig,
)


class ApprovalCompilerError(ValueError):
    """Raised when approval configuration cannot be compiled into valid BPMN."""


class ApprovalBpmnCompiler:
    """Compiler transforming typed ApprovalWorkflowConfig into secure BPMN 2.0 XML."""

    def compile(self, config: ApprovalWorkflowConfig) -> str:
        """Generate valid BPMN 2.0 XML string from config."""
        if not config.steps:
            raise ApprovalCompilerError("Approval workflow must contain at least one step.")

        # Validate quorum settings across all steps
        for step in config.steps:
            if (
                step.pattern == ApprovalPattern.QUORUM_N_OF_M
                or config.approval_type == ApprovalType.QUORUM_N_OF_M
            ):
                q = step.required_quorum or config.quorum_count
                if q is None or q < 1:
                    raise ApprovalCompilerError(
                        "QUORUM_N_OF_M pattern requires required_quorum >= 1"
                    )

        if len(config.steps) == 1:
            step = config.steps[0]
            step_pattern = step.pattern or config.approval_type or ApprovalPattern.SINGLE
            if step_pattern == ApprovalPattern.QUORUM_N_OF_M:
                return self._compile_quorum(config)
            elif step_pattern == ApprovalPattern.PARALLEL_ALL_OF:
                if len(step.candidate_roles) > 1:
                    return self._compile_parallel_roles(config, step)
                return self._compile_single(config)
            elif step_pattern in (ApprovalPattern.SINGLE, ApprovalPattern.ROLE_POOL):
                return self._compile_single(config)
            else:
                return self._compile_single(config)
        else:
            workflow_pattern = (
                config.approval_type or config.steps[0].pattern or ApprovalPattern.SEQUENTIAL
            )
            if workflow_pattern == ApprovalPattern.PARALLEL_ALL_OF:
                return self._compile_parallel_all_of(config)
            return self._compile_sequential(config)

    def _compile_single(self, config: ApprovalWorkflowConfig) -> str:
        step = config.steps[0]
        step_id = step.effective_id
        step_name = escape(step.effective_name)
        assignee_attr = f' flowable:assignee="{escape(step.assignee)}"' if step.assignee else ""
        candidate_groups = ",".join(step.candidate_roles) if step.candidate_roles else ""
        cg_attr = (
            f' flowable:candidateGroups="{escape(candidate_groups)}"' if candidate_groups else ""
        )
        form_attr = f' flowable:formKey="{escape(step.form_key)}"' if step.form_key else ""

        sla_elements = ""
        if step.sla_policy_key:
            sla_elements = f"""
    <boundaryEvent id="timer_warning_{step_id}" attachedToRef="{step_id}" cancelActivity="false">
      <timerEventDefinition>
        <timeDate>${{sla_warning_at}}</timeDate>
      </timerEventDefinition>
    </boundaryEvent>
    <sequenceFlow id="flow_warn_{step_id}"
                  sourceRef="timer_warning_{step_id}"
                  targetRef="end_warn_{step_id}" />
    <endEvent id="end_warn_{step_id}" />
    <boundaryEvent id="timer_breach_{step_id}" attachedToRef="{step_id}" cancelActivity="true">
      <timerEventDefinition>
        <timeDate>${{sla_breach_at}}</timeDate>
      </timerEventDefinition>
    </boundaryEvent>
    <sequenceFlow id="flow_breach_{step_id}"
                  sourceRef="timer_breach_{step_id}"
                  targetRef="end_breach_{step_id}" />
    <endEvent id="end_breach_{step_id}" />"""

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:flowable="http://flowable.org/bpmn"
             targetNamespace="http://e7.operations.engine/workflows">
  <process id="{config.effective_key}" name="{escape(config.effective_name)}" isExecutable="true">
    <startEvent id="start" />
    <sequenceFlow id="flow_start_task" sourceRef="start" targetRef="{step_id}" />
    <userTask id="{step_id}" name="{step_name}"{assignee_attr}{cg_attr}{form_attr} />{sla_elements}
    <sequenceFlow id="flow_task_end" sourceRef="{step_id}" targetRef="end" />
    <endEvent id="end" />
  </process>
</definitions>"""

    def _compile_sequential(self, config: ApprovalWorkflowConfig) -> str:
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"',
            '             xmlns:flowable="http://flowable.org/bpmn"',
            '             targetNamespace="http://e7.operations.engine/workflows">',
            (
                f'  <process id="{config.effective_key}" '
                f'name="{escape(config.effective_name)}" isExecutable="true">'
            ),
            '    <startEvent id="start" />',
        ]

        prev_node = "start"
        for step in config.steps:
            step_id = step.effective_id
            step_name = escape(step.effective_name)
            assignee_attr = f' flowable:assignee="{escape(step.assignee)}"' if step.assignee else ""
            candidate_groups = ",".join(step.candidate_roles) if step.candidate_roles else ""
            cg_attr = (
                f' flowable:candidateGroups="{escape(candidate_groups)}"'
                if candidate_groups
                else ""
            )
            form_attr = f' flowable:formKey="{escape(step.form_key)}"' if step.form_key else ""

            flow_id = f"flow_{prev_node}_{step_id}"
            lines.append(
                f'    <sequenceFlow id="{flow_id}" sourceRef="{prev_node}" targetRef="{step_id}" />'
            )
            lines.append(
                f'    <userTask id="{step_id}" name="{step_name}"'
                f"{assignee_attr}{cg_attr}{form_attr} />"
            )
            prev_node = step_id

        final_flow = f"flow_{prev_node}_end"
        lines.append(
            f'    <sequenceFlow id="{final_flow}" sourceRef="{prev_node}" targetRef="end" />'
        )
        lines.append('    <endEvent id="end" />')
        lines.append("  </process>")
        lines.append("</definitions>")
        return "\n".join(lines)

    def _compile_parallel_roles(
        self, config: ApprovalWorkflowConfig, step: ApprovalStepConfig
    ) -> str:
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"',
            '             xmlns:flowable="http://flowable.org/bpmn"',
            '             targetNamespace="http://e7.operations.engine/workflows">',
            (
                f'  <process id="{config.effective_key}" '
                f'name="{escape(config.effective_name)}" isExecutable="true">'
            ),
            '    <startEvent id="start" />',
            '    <sequenceFlow id="flow_start_fork" sourceRef="start" targetRef="fork" />',
            '    <parallelGateway id="fork" />',
        ]

        step_base = step.effective_id
        for role in step.candidate_roles:
            sub_id = f"{step_base}_{role}"
            sub_name = escape(f"{step.effective_name} ({role})")
            cg_attr = f' flowable:candidateGroups="{escape(role)}"'
            form_attr = f' flowable:formKey="{escape(step.form_key)}"' if step.form_key else ""

            lines.append(
                f'    <sequenceFlow id="flow_fork_{sub_id}" '
                f'sourceRef="fork" targetRef="{sub_id}" />'
            )
            lines.append(f'    <userTask id="{sub_id}" name="{sub_name}"{cg_attr}{form_attr} />')
            lines.append(
                f'    <sequenceFlow id="flow_{sub_id}_join" '
                f'sourceRef="{sub_id}" targetRef="join" />'
            )

        lines.append('    <parallelGateway id="join" />')
        lines.append('    <sequenceFlow id="flow_join_end" sourceRef="join" targetRef="end" />')
        lines.append('    <endEvent id="end" />')
        lines.append("  </process>")
        lines.append("</definitions>")
        return "\n".join(lines)

    def _compile_parallel_all_of(self, config: ApprovalWorkflowConfig) -> str:
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"',
            '             xmlns:flowable="http://flowable.org/bpmn"',
            '             targetNamespace="http://e7.operations.engine/workflows">',
            (
                f'  <process id="{config.effective_key}" '
                f'name="{escape(config.effective_name)}" isExecutable="true">'
            ),
            '    <startEvent id="start" />',
            '    <sequenceFlow id="flow_start_fork" sourceRef="start" targetRef="fork" />',
            '    <parallelGateway id="fork" />',
        ]

        for step in config.steps:
            step_id = step.effective_id
            step_name = escape(step.effective_name)
            assignee_attr = f' flowable:assignee="{escape(step.assignee)}"' if step.assignee else ""
            candidate_groups = ",".join(step.candidate_roles) if step.candidate_roles else ""
            cg_attr = (
                f' flowable:candidateGroups="{escape(candidate_groups)}"'
                if candidate_groups
                else ""
            )
            form_attr = f' flowable:formKey="{escape(step.form_key)}"' if step.form_key else ""

            lines.append(
                f'    <sequenceFlow id="flow_fork_{step_id}" '
                f'sourceRef="fork" targetRef="{step_id}" />'
            )
            task_elem = (
                f'    <userTask id="{step_id}" name="{step_name}"'
                f"{assignee_attr}{cg_attr}{form_attr} />"
            )
            lines.append(task_elem)
            lines.append(
                f'    <sequenceFlow id="flow_{step_id}_join" '
                f'sourceRef="{step_id}" targetRef="join" />'
            )

        lines.append('    <parallelGateway id="join" />')
        lines.append('    <sequenceFlow id="flow_join_end" sourceRef="join" targetRef="end" />')
        lines.append('    <endEvent id="end" />')
        lines.append("  </process>")
        lines.append("</definitions>")
        return "\n".join(lines)

    def _compile_quorum(self, config: ApprovalWorkflowConfig) -> str:
        step = config.steps[0]
        step_id = step.effective_id
        step_name = escape(step.effective_name)
        q_count = step.required_quorum or config.quorum_count or 2
        assignee_attr = f' flowable:assignee="{escape(step.assignee)}"' if step.assignee else ""
        candidate_groups = ",".join(step.candidate_roles) if step.candidate_roles else ""
        cg_attr = (
            f' flowable:candidateGroups="{escape(candidate_groups)}"' if candidate_groups else ""
        )
        form_attr = f' flowable:formKey="{escape(step.form_key)}"' if step.form_key else ""

        completion_expr = f"${{nrOfCompletedInstances &gt;= {q_count}}}"

        total_participants = (
            len(step.candidate_roles)
            if len(step.candidate_roles) >= q_count
            else max(3, q_count + 1)
        )

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:flowable="http://flowable.org/bpmn"
             targetNamespace="http://e7.operations.engine/workflows">
  <process id="{config.effective_key}" name="{escape(config.effective_name)}" isExecutable="true">
    <startEvent id="start" />
    <sequenceFlow id="flow_start_quorum" sourceRef="start" targetRef="{step_id}" />
    <userTask id="{step_id}" name="{step_name}"{assignee_attr}{cg_attr}{form_attr}>
      <multiInstanceLoopCharacteristics isSequential="false">
        <loopCardinality>{total_participants}</loopCardinality>
        <completionCondition>{completion_expr}</completionCondition>
      </multiInstanceLoopCharacteristics>
    </userTask>
    <sequenceFlow id="flow_quorum_end" sourceRef="{step_id}" targetRef="end" />
    <endEvent id="end" />
  </process>
</definitions>"""
