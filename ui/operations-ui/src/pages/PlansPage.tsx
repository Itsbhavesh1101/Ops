import React, { useState, useEffect, useCallback } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { FileSpreadsheet, ArrowRight, RefreshCw, Hash, CheckCircle2 } from "lucide-react";
import { apiClient } from "../api/client";
import { formatStatus, formatDate, formatShortId } from "../utils/formatters";
import type { PlanResponse } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { LoadingState } from "../components/ui/LoadingState";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";

export const PlansPage: React.FC = () => {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const goalIdFilter = searchParams.get("goal_id");

  const [plans, setPlans] = useState<PlanResponse[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const tenantId = user?.tenant_id || "tenant-engineering";

  const fetchPlans = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiClient.listPlans(
        goalIdFilter ? { goal_id: goalIdFilter } : undefined,
        tenantId
      );
      setPlans(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load operational plans";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [goalIdFilter, tenantId]);

  useEffect(() => {
    fetchPlans();
  }, [fetchPlans]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Operational Plans"
        description="Synthesized Work Breakdown Structures (WBS), task sequencing, and critical paths."
        actions={
          <div className="flex items-center gap-3">
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchPlans}
              disabled={loading}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            <Link to="/goals">
              <Button variant="primary" size="sm" className="flex items-center gap-1.5">
                Generate Plan via Goal
              </Button>
            </Link>
          </div>
        }
      />

      {goalIdFilter && (
        <div className="flex items-center justify-between p-3 rounded-lg bg-indigo-950/40 border border-indigo-900/60 text-xs text-indigo-300">
          <span>Filtering plans for Goal ID: <strong className="font-mono">{goalIdFilter}</strong></span>
          <Link to="/plans" className="text-indigo-400 hover:text-indigo-200 underline">
            Clear Filter
          </Link>
        </div>
      )}

      {loading && <LoadingState message="Fetching operational plans from engine..." />}

      {error && !loading && (
        <ErrorState
          title="Failed to Load Plans"
          message={error}
          onRetry={fetchPlans}
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
        <div className="space-y-3">
          <div className="grid grid-cols-1 gap-3">
            {plans.map((plan) => (
              <Card
                key={plan.id}
                className="p-5 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:border-slate-700 transition-colors"
              >
                <div className="space-y-2">
                  <div className="flex items-center gap-2.5 flex-wrap">
                    <span className="p-1.5 rounded-lg bg-indigo-500/10 text-indigo-400">
                      <FileSpreadsheet className="w-4 h-4" />
                    </span>
                    <h3 className="text-sm font-semibold text-white">
                      {plan.plan_name}
                    </h3>
                    <Badge variant="neutral">v{plan.version_number}</Badge>
                    <Badge
                      variant={
                        plan.status === "APPROVED" || plan.status === "ACTIVE"
                          ? "success"
                          : plan.status === "DRAFT"
                          ? "neutral"
                          : "warning"
                      }
                    >
                      {formatStatus(plan.status)}
                    </Badge>
                    {plan.is_baseline && (
                      <Badge variant="info" className="flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3" /> Baselined
                      </Badge>
                    )}
                  </div>

                  <div className="flex items-center gap-4 text-xs text-slate-400 flex-wrap">
                    <span className="font-mono text-[11px]">Plan ID: {formatShortId(plan.id)}</span>
                    <span>
                      Goal:{" "}
                      <Link
                        to={`/goals/${plan.goal_id}`}
                        className="text-indigo-400 hover:underline font-mono text-[11px]"
                      >
                        {formatShortId(plan.goal_id)}
                      </Link>
                    </span>
                    {plan.baseline_hash && (
                      <span className="flex items-center gap-1 font-mono text-[11px] text-slate-400">
                        <Hash className="w-3 h-3 text-slate-400" />
                        {plan.baseline_hash.substring(0, 10)}...
                      </span>
                    )}
                    <span className="text-[11px]">
                      Created {formatDate(plan.created_at)}
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <Link
                    to={`/plans/${plan.id}`}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-indigo-400 hover:text-indigo-300"
                  >
                    Inspect WBS & Tasks <ArrowRight className="w-4 h-4" />
                  </Link>
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
