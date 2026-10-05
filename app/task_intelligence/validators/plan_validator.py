"""Deterministic PlanValidator pipeline.

Validates PlanSchema structural integrity, ID uniqueness, WBS hierarchy,
dependency references, DAG acyclicity via NetworkX, and estimates.
"""

from collections.abc import Callable
from typing import Any

from app.task_intelligence.graph import (
    CriticalPathCalculator,
    TaskDependencyGraph,
)
from app.task_intelligence.schemas import (
    PlanSchema,
    ValidationIssue,
    ValidationIssueCode,
    ValidationResult,
    ValidationSeverity,
)

PolicyHook = Callable[[PlanSchema], list[ValidationIssue]]


class PlanValidator:
    """Deterministic validation engine for PlanSchema proposals."""

    def __init__(self, policy_hooks: list[PolicyHook] | None = None) -> None:
        self._policy_hooks = policy_hooks or []

    def validate(self, plan: PlanSchema) -> ValidationResult:
        """Run all deterministic validation checks on the given PlanSchema.

        Args:
            plan: The PlanSchema proposal to validate.

        Returns:
            ValidationResult with machine-readable issues, cycle path if any,
            topological order, and critical path.
        """
        issues: list[ValidationIssue] = []

        if not plan.tasks:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.EMPTY_PLAN,
                    location="tasks",
                    message="Plan must contain at least one task.",
                    severity=ValidationSeverity.ERROR,
                    repairable=False,
                )
            )
            return ValidationResult(
                is_valid=False,
                issues=issues,
                evaluated_task_count=0,
                evaluated_dependency_count=len(plan.dependencies),
            )

        task_id_set: set[str] = set()
        task_map: dict[str, Any] = {}

        # 1. Task ID uniqueness & structural checks
        for idx, task in enumerate(plan.tasks):
            loc = f"tasks[{task.temporary_id}]"
            if task.temporary_id in task_id_set:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.DUPLICATE_TASK_ID,
                        location=f"tasks[{idx}].temporary_id",
                        message=f"Duplicate task temporary ID: '{task.temporary_id}'.",
                        severity=ValidationSeverity.ERROR,
                        repairable=True,
                    )
                )
            else:
                task_id_set.add(task.temporary_id)
                task_map[task.temporary_id] = task

            # Duration and estimate checks
            if task.estimated_duration_minutes < 0:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.INVALID_DURATION,
                        location=f"{loc}.estimated_duration_minutes",
                        message=(
                            f"Task duration cannot be negative: {task.estimated_duration_minutes}."
                        ),
                        severity=ValidationSeverity.ERROR,
                        repairable=True,
                    )
                )

            if task.effort_estimate:
                if task.effort_estimate.value < 0:
                    issues.append(
                        ValidationIssue(
                            code=ValidationIssueCode.INVALID_DURATION,
                            location=f"{loc}.effort_estimate.value",
                            message=(
                                f"Effort value cannot be negative: {task.effort_estimate.value}."
                            ),
                            severity=ValidationSeverity.ERROR,
                            repairable=True,
                        )
                    )
                if not (0.0 <= task.effort_estimate.confidence <= 1.0):
                    issues.append(
                        ValidationIssue(
                            code=ValidationIssueCode.INVALID_CONFIDENCE,
                            location=f"{loc}.effort_estimate.confidence",
                            message=(
                                f"Confidence score must be between 0.0 and 1.0: "
                                f"{task.effort_estimate.confidence}."
                            ),
                            severity=ValidationSeverity.ERROR,
                            repairable=True,
                        )
                    )

        # 2. Parent-child hierarchy checks
        for task in plan.tasks:
            loc = f"tasks[{task.temporary_id}].parent_id"
            if task.parent_id is not None:
                if task.parent_id == task.temporary_id:
                    issues.append(
                        ValidationIssue(
                            code=ValidationIssueCode.PARENT_SELF_REFERENCE,
                            location=loc,
                            message=f"Task '{task.temporary_id}' cannot be its own parent.",
                            severity=ValidationSeverity.ERROR,
                            repairable=True,
                        )
                    )
                elif task.parent_id not in task_id_set:
                    issues.append(
                        ValidationIssue(
                            code=ValidationIssueCode.PARENT_NOT_FOUND,
                            location=loc,
                            message=(
                                f"Task '{task.temporary_id}' references non-existent parent "
                                f"'{task.parent_id}'."
                            ),
                            severity=ValidationSeverity.ERROR,
                            repairable=True,
                        )
                    )
                else:
                    # Detect parent cycles
                    visited: set[str] = {task.temporary_id}
                    curr = task.parent_id
                    while curr is not None and curr in task_map:
                        if curr in visited:
                            issues.append(
                                ValidationIssue(
                                    code=ValidationIssueCode.PARENT_CYCLE,
                                    location=loc,
                                    message=(
                                        f"Cycle detected in parent hierarchy involving "
                                        f"'{task.temporary_id}' and '{curr}'."
                                    ),
                                    severity=ValidationSeverity.ERROR,
                                    repairable=True,
                                )
                            )
                            break
                        visited.add(curr)
                        curr = task_map[curr].parent_id

        # 3. Milestone checks
        milestone_id_set: set[str] = set()
        for idx, milestone in enumerate(plan.milestones):
            if milestone.temporary_id in milestone_id_set:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.DUPLICATE_MILESTONE_ID,
                        location=f"milestones[{idx}].temporary_id",
                        message=f"Duplicate milestone ID: '{milestone.temporary_id}'.",
                        severity=ValidationSeverity.ERROR,
                        repairable=True,
                    )
                )
            else:
                milestone_id_set.add(milestone.temporary_id)

            for tid in milestone.associated_task_ids:
                if tid not in task_id_set:
                    issues.append(
                        ValidationIssue(
                            code=ValidationIssueCode.UNKNOWN_MILESTONE_TASK,
                            location=f"milestones[{milestone.temporary_id}].associated_task_ids",
                            message=(
                                f"Milestone '{milestone.temporary_id}' references "
                                f"unknown task '{tid}'."
                            ),
                            severity=ValidationSeverity.ERROR,
                            repairable=True,
                        )
                    )

        # 4. Dependency reference & graph checks
        seen_edges: set[tuple[str, str]] = set()
        graph = TaskDependencyGraph()
        for task in plan.tasks:
            if task.temporary_id in task_id_set:
                graph.add_task(
                    task_id=task.temporary_id,
                    duration_minutes=task.estimated_duration_minutes,
                )

        has_dep_ref_errors = False
        for idx, dep in enumerate(plan.dependencies):
            loc = f"dependencies[{idx}]"
            # Self-dependency
            if dep.predecessor_id == dep.successor_id:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.SELF_DEPENDENCY,
                        location=loc,
                        message=(
                            f"Self-dependency rejected: '{dep.predecessor_id}' "
                            f"cannot depend on itself."
                        ),
                        severity=ValidationSeverity.ERROR,
                        repairable=True,
                    )
                )
                has_dep_ref_errors = True
                continue

            # Reference existence
            pred_exists = dep.predecessor_id in task_id_set
            succ_exists = dep.successor_id in task_id_set

            if not pred_exists:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.UNKNOWN_TASK_REFERENCE,
                        location=f"{loc}.predecessor_id",
                        message=(
                            f"Dependency predecessor '{dep.predecessor_id}' "
                            f"does not exist in tasks."
                        ),
                        severity=ValidationSeverity.ERROR,
                        repairable=True,
                    )
                )
                has_dep_ref_errors = True

            if not succ_exists:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.UNKNOWN_TASK_REFERENCE,
                        location=f"{loc}.successor_id",
                        message=(
                            f"Dependency successor '{dep.successor_id}' does not exist in tasks."
                        ),
                        severity=ValidationSeverity.ERROR,
                        repairable=True,
                    )
                )
                has_dep_ref_errors = True

            if not (pred_exists and succ_exists):
                continue

            # Duplicate dependency
            edge = (dep.predecessor_id, dep.successor_id)
            if edge in seen_edges:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.DUPLICATE_DEPENDENCY,
                        location=loc,
                        message=(
                            f"Duplicate dependency edge: '{dep.predecessor_id}' -> "
                            f"'{dep.successor_id}'."
                        ),
                        severity=ValidationSeverity.ERROR,
                        repairable=True,
                    )
                )
            else:
                seen_edges.add(edge)
                graph.add_dependency(
                    predecessor_id=dep.predecessor_id,
                    successor_id=dep.successor_id,
                    lag_minutes=dep.lag_minutes,
                )

        # 5. Cycle Detection and CPM (only if no reference errors broke the graph)
        cycle_path: list[str] | None = None
        topological_order: list[str] | None = None
        critical_path: list[str] | None = None

        if not has_dep_ref_errors:
            cycle_res = graph.detect_cycle()
            if cycle_res.has_cycle:
                cycle_path = cycle_res.cycle_path
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.CYCLE_DETECTED,
                        location="dependencies",
                        message=f"Cyclic dependency detected: {' -> '.join(cycle_path or [])}.",
                        severity=ValidationSeverity.ERROR,
                        repairable=True,
                        context={"cycle_path": cycle_path},
                    )
                )
            else:
                topological_order = graph.topological_sort()
                cpm_res = CriticalPathCalculator.calculate(graph)
                critical_path = cpm_res.critical_path

        # 6. Policy hooks
        for hook in self._policy_hooks:
            hook_issues = hook(plan)
            issues.extend(hook_issues)

        is_valid = not any(issue.severity == ValidationSeverity.ERROR for issue in issues)

        return ValidationResult(
            is_valid=is_valid,
            issues=issues,
            evaluated_task_count=len(plan.tasks),
            evaluated_dependency_count=len(plan.dependencies),
            cycle_path=cycle_path,
            topological_order=topological_order,
            critical_path=critical_path,
        )
