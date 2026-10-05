"""Infeasibility diagnostic explainer and conflict extraction.

Part of Phase 4A Generic Scheduling Core.
Translates solver sufficient conflict sets and pre-solve structural impossibilities
into canonical conflict diagnostics, human-readable explanations, and
relaxation suggestions for human review.
"""

from typing import Any

from ortools.sat.python import cp_model

from app.scheduling.constraints.builders import ModelBuildContext
from app.scheduling.constraints.models import ConstraintType
from app.scheduling.diagnostics.models import InfeasibilityDiagnostic


class InfeasibilityExplainer:
    """Extracts small practical conflict sets and produces deterministic explanations."""

    @classmethod
    def explain(
        cls,
        solver: cp_model.CpSolver,
        ctx: ModelBuildContext,
    ) -> list[InfeasibilityDiagnostic]:
        """Analyze solver state and return structured conflict diagnostics."""
        diagnostics: list[InfeasibilityDiagnostic] = []

        # 1. Check for pre-solve structural incompatibilities
        # (e.g. 0 eligible resources for an activity)
        if ctx.pre_solve_infeasibilities:
            for item in ctx.pre_solve_infeasibilities:
                diagnostics.append(
                    InfeasibilityDiagnostic(
                        root_cause_type=item.get(
                            "root_cause_type", "RESOURCE_ELIGIBILITY_UNSATISFIABLE"
                        ),
                        human_readable_explanation=item.get(
                            "explanation", "No eligible resources available for requested skills."
                        ),
                        conflicting_constraint_ids=[f"eligibility_{item.get('activity_id')}"],
                        affected_activity_ids=[item.get("activity_id", "")],
                        affected_resource_ids=[],
                        relaxation_candidates=[
                            "split the work into separate skill-specific work orders",
                            "provide a person resource possessing both required capabilities",
                            "change the required capability combination if business policy allows",
                        ],
                    )
                )
            return diagnostics

        # 2. Extract CP-SAT assumption core
        raw_core = solver.SufficientAssumptionsForInfeasibility()
        conflicting_constraints: list[dict[str, Any]] = []
        affected_activities: set[str] = set()
        affected_resources: set[str] = set()
        c_ids: list[str] = []

        for lit_index in raw_core:
            if lit_index in ctx.assumption_literals:
                info = ctx.assumption_literals[lit_index]
                conflicting_constraints.append(info)
                c_ids.append(info["constraint_id"])
                details = info.get("details", {})
                if "activity_id" in details:
                    affected_activities.add(details["activity_id"])
                if "resource_id" in details:
                    affected_resources.add(details["resource_id"])
                if "predecessor_id" in details:
                    affected_activities.add(details["predecessor_id"])
                if "successor_id" in details:
                    affected_activities.add(details["successor_id"])

        if not conflicting_constraints:
            # Fallback if no specific assumption was flagged
            return [
                InfeasibilityDiagnostic(
                    root_cause_type="UNSATISFIABLE_CONSTRAINTS",
                    human_readable_explanation=(
                        "The scheduling problem has no feasible solution satisfying all "
                        "hard constraints within the configured planning horizon."
                    ),
                    conflicting_constraint_ids=[],
                    affected_activity_ids=list(ctx.act_vars.keys()),
                    affected_resource_ids=list(ctx.resources_by_id.keys()),
                    relaxation_candidates=[
                        "expand planning horizon",
                        "relax optional minimum gap constraints",
                        "add additional resource capacity",
                    ],
                )
            ]

        # Categorize primary conflict from identified assumptions
        primary_types = {c["constraint_type"] for c in conflicting_constraints}
        root_cause = "CONSTRAINT_CONFLICT"
        explanation_parts: list[str] = []
        relaxations: list[str] = []

        if ConstraintType.TIME_WINDOW in primary_types:
            root_cause = "TIME_WINDOW_VIOLATION"
            explanation_parts.append(
                "One or more activities cannot fit within their allowed time windows."
            )
            relaxations.append("expand activity time windows")

        if ConstraintType.NO_OVERLAP in primary_types:
            root_cause = "RESOURCE_OVERLAP_CONFLICT"
            explanation_parts.append(
                "Activities compete for exclusive access to shared resources "
                "without sufficient separation."
            )
            relaxations.append("assign alternate eligible resources")
            relaxations.append("shift non-critical activities to alternative windows")

        if ConstraintType.PRECEDENCE in primary_types:
            root_cause = "PRECEDENCE_ORDERING_CONFLICT"
            explanation_parts.append(
                "Precedence orderings cannot be satisfied in available horizon duration."
            )
            relaxations.append("relax optional gap between dependent activities")

        if ConstraintType.LOCK in primary_types:
            root_cause = "LOCK_INCOMPATIBILITY"
            explanation_parts.append(
                "Fixed locks on activities conflict with other hard constraints."
            )
            relaxations.append("unlock fixed assignments for rescheduling")

        if not explanation_parts:
            explanation_parts.append(
                "Identified conflicting constraint set prevents a feasible assignment."
            )
            relaxations.append("review conflicting constraint parameters")

        diagnostics.append(
            InfeasibilityDiagnostic(
                root_cause_type=root_cause,
                human_readable_explanation=" ".join(explanation_parts),
                conflicting_constraint_ids=sorted(set(c_ids)),
                affected_activity_ids=sorted(affected_activities),
                affected_resource_ids=sorted(affected_resources),
                relaxation_candidates=relaxations,
            )
        )
        return diagnostics
