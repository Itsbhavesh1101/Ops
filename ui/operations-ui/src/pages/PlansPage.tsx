import React, { useState, useEffect, useCallback } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRight, RefreshCw, Plus } from "lucide-react";
import { apiClient } from "../api/client";
import { formatStatus, formatDate, formatShortId } from "../utils/formatters";
import type { PlanResponse, GoalResponse } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "../components/ui/Table";
import { LoadingState } from "../components/ui/LoadingState";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";

export const PlansPage: React.FC = () => {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const goalIdFilter = searchParams.get("goal_id");

  const [plans, setPlans] = useState<PlanResponse[]>([]);
  const [goalsMap, setGoalsMap] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const tenantId = user?.tenant_id || "tenant-engineering";

  const fetchPlansAndGoals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [plansData, goalsData] = await Promise.all([
        apiClient.listPlans(
          goalIdFilter ? { goal_id: goalIdFilter } : undefined,
          tenantId
        ),
        apiClient.listGoals(undefined, tenantId).catch(() => [] as GoalResponse[]),
      ]);
      setPlans(plansData);
      const mapping: Record<string, string> = {};
      goalsData.forEach((g) => {
        mapping[g.id] = g.objective;
      });
      setGoalsMap(mapping);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load operational plans";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [goalIdFilter, tenantId]);

  useEffect(() => {
    fetchPlansAndGoals();
  }, [fetchPlansAndGoals]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Operational Plans"
        description="Synthesized Work Breakdown Structures (WBS), task sequencing, and critical paths."
        actions={
          <div className="flex items-center gap-2.5">
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchPlansAndGoals}
              disabled={loading}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            <Link to="/goals" className="no-underline">
              <Button variant="primary" size="sm" className="flex items-center gap-1.5">
                <Plus className="w-3.5 h-3.5" />
                Generate Plan
              </Button>
            </Link>
          </div>
        }
      />

      {goalIdFilter && (
        <div className="flex items-center justify-between p-3 rounded-lg bg-indigo-50 border border-indigo-200 text-xs text-indigo-900">
          <span>Filtering plans for Goal: <strong>{goalsMap[goalIdFilter] || formatShortId(goalIdFilter)}</strong></span>
          <Link to="/plans" className="text-indigo-600 hover:text-indigo-800 font-semibold no-underline">
            Clear Filter
          </Link>
        </div>
      )}

      {loading && <LoadingState message="Fetching operational plans from engine..." />}

      {error && !loading && (
        <ErrorState
          title="Failed to Load Plans"
          message={error}
          onRetry={fetchPlansAndGoals}
        />
      )}

      {!loading && !error && plans.length === 0 && (
        <EmptyState
          title="No Operational Plans Found"
          description="Operational plans are synthesized from approved operational goals."
          actionLabel="Go to Goals to Synthesize Plan"
          onAction={() => navigate("/goals")}
        />
      )}

      {!loading && !error && plans.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Plan</TableHead>
              <TableHead>Goal</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Version</TableHead>
              <TableHead>Baseline</TableHead>
              <TableHead>Updated</TableHead>
              <TableHead className="text-right">Action</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {plans.map((plan) => (
              <TableRow key={plan.id} className="hover:bg-slate-50/70 transition-colors">
                <TableCell>
                  <Link
                    to={`/plans/${plan.id}`}
                    className="font-medium text-slate-900 hover:text-indigo-600 transition-colors no-underline block"
                  >
                    {plan.plan_name}
                  </Link>
                </TableCell>
                <TableCell>
                  <Link
                    to={`/goals/${plan.goal_id}`}
                    className="text-xs text-slate-700 hover:text-indigo-600 font-medium no-underline hover:underline line-clamp-1 max-w-xs"
                    title={goalsMap[plan.goal_id] || plan.goal_id}
                  >
                    {goalsMap[plan.goal_id] || formatShortId(plan.goal_id)}
                  </Link>
                </TableCell>
                <TableCell>
                  <Badge
                    variant={
                      plan.status === "APPROVED" || plan.status === "ACTIVE"
                        ? "success"
                        : plan.status === "DRAFT"
                        ? "neutral"
                        : "warning"
                    }
                    size="sm"
                  >
                    {formatStatus(plan.status)}
                  </Badge>
                </TableCell>
                <TableCell>
                  <span className="font-mono text-xs font-semibold text-slate-700 bg-slate-100 px-2 py-0.5 rounded">
                    v{plan.version_number}
                  </span>
                </TableCell>
                <TableCell>
                  {plan.is_baseline ? (
                    <Badge variant="info" size="sm" dot>
                      Baselined
                    </Badge>
                  ) : (
                    <span className="text-slate-400 text-xs">—</span>
                  )}
                </TableCell>
                <TableCell>
                  <span className="text-xs text-slate-600">
                    {formatDate(plan.created_at)}
                  </span>
                </TableCell>
                <TableCell className="text-right">
                  <Link
                    to={`/plans/${plan.id}`}
                    className="inline-flex items-center gap-1 text-xs font-medium text-indigo-600 hover:text-indigo-800 no-underline px-2 py-1 rounded hover:bg-indigo-50/50 transition-colors"
                  >
                    <span>View</span>
                    <ArrowRight className="w-3.5 h-3.5" />
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
};
