import React, { useState, useEffect, useCallback } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  Target,
  FileSpreadsheet,
  Calendar,
  Clock,
  ShieldAlert,
  Layers,
  ArrowRight,
  Sparkles,
  RefreshCw,
  Hash,
} from "lucide-react";
import { apiClient } from "../api/client";
import { formatStatus, formatDateTime, formatDate, formatShortId } from "../utils/formatters";
import type { GoalResponse, PlanResponse } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../components/ui/Toast";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { LoadingState } from "../components/ui/LoadingState";
import { ErrorState } from "../components/ui/ErrorState";

export const GoalDetailPage: React.FC = () => {
  const { goalId } = useParams<{ goalId: string }>();
  const { user } = useAuth();
  const { showToast } = useToast();
  const navigate = useNavigate();

  const [goal, setGoal] = useState<GoalResponse | null>(null);
  const [plans, setPlans] = useState<PlanResponse[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [isGeneratingPlan, setIsGeneratingPlan] = useState<boolean>(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  const tenantId = user?.tenant_id || "tenant-engineering";

  const fetchGoalData = useCallback(async () => {
    if (!goalId) return;
    setLoading(true);
    setError(null);
    try {
      const [goalData, plansData] = await Promise.all([
        apiClient.getGoal(goalId, tenantId),
        apiClient.listPlans({ goal_id: goalId }, tenantId),
      ]);
      setGoal(goalData);
      setPlans(plansData);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load operational goal details";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [goalId, tenantId]);

  useEffect(() => {
    fetchGoalData();
  }, [fetchGoalData]);

  const handleGeneratePlan = async () => {
    if (!goalId) return;
    setIsGeneratingPlan(true);
    setGenerateError(null);

    try {
      const generated = await apiClient.generatePlan(goalId, null, tenantId);
      showToast(
        "success",
        `Plan "${generated.plan_name}" (v${generated.version_number}) synthesized successfully.`,
        "Plan Generated"
      );
      navigate(`/plans/${generated.id}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Plan synthesis failed";
      setGenerateError(msg);
      showToast("error", msg, "Synthesis Error");
    } finally {
      setIsGeneratingPlan(false);
    }
  };

  if (loading) {
    return <LoadingState message="Loading operational goal metadata..." />;
  }

  if (error || !goal) {
    return (
      <ErrorState
        title="Goal Not Found or Unavailable"
        message={error || `Goal ID ${goalId} could not be retrieved.`}
        onRetry={fetchGoalData}
      />
    );
  }

  const breadcrumbs = [
    { label: "Goals", href: "/goals" },
    { label: goal.objective ? (goal.objective.length > 32 ? `${goal.objective.substring(0, 32)}...` : goal.objective) : goal.id.substring(0, 8) },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        breadcrumbs={breadcrumbs}
        title={goal.objective}
        description={goal.scope || "Operational goal metadata and automated plan synthesis controls."}
        badge={
          goal.priority_target ? (
            <Badge
              variant={
                goal.priority_target.toUpperCase() === "HIGH" ||
                goal.priority_target.toUpperCase() === "CRITICAL"
                  ? "warning"
                  : "info"
              }
              size="sm"
            >
              {goal.priority_target}
            </Badge>
          ) : undefined
        }
        actions={
          <div className="flex items-center gap-2.5">
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchGoalData}
              disabled={loading || isGeneratingPlan}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Refresh
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleGeneratePlan}
              disabled={isGeneratingPlan}
              className="flex items-center gap-2"
            >
              <Sparkles className={`w-3.5 h-3.5 ${isGeneratingPlan ? "animate-spin" : ""}`} />
              {isGeneratingPlan ? "Synthesizing Plan..." : "Generate Plan"}
            </Button>
          </div>
        }
      />

      {generateError && (
        <div className="p-3.5 text-xs rounded-lg bg-rose-50 border border-rose-200 text-rose-700">
          <p className="font-semibold mb-0.5">Plan Generation Error</p>
          <p>{generateError}</p>
        </div>
      )}

      {/* Goal Details Split Panes */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        <Card className="p-5 md:col-span-2 space-y-4">
          <div className="flex items-center gap-2 border-b border-slate-100 pb-3">
            <Target className="w-4 h-4 text-indigo-600" />
            <h2 className="text-sm font-semibold text-slate-900">
              Operational Objective & Boundaries
            </h2>
          </div>

          <div className="space-y-4 text-xs">
            <div>
              <span className="text-slate-500 font-medium block mb-1">Objective:</span>
              <p className="text-slate-900 text-sm leading-relaxed font-semibold">
                {goal.objective}
              </p>
            </div>

            {goal.scope && (
              <div>
                <span className="text-slate-500 font-medium block mb-1">Scope:</span>
                <p className="text-slate-700 leading-relaxed">{goal.scope}</p>
              </div>
            )}

            {goal.constraints && goal.constraints.length > 0 && (
              <div>
                <span className="text-slate-500 font-medium flex items-center gap-1.5 mb-1.5">
                  <ShieldAlert className="w-3.5 h-3.5 text-amber-600" /> Active Constraints:
                </span>
                <ul className="list-disc list-inside space-y-1 text-slate-700 pl-1">
                  {goal.constraints.map((c, idx) => (
                    <li key={idx} className="font-mono text-[11px]">{c}</li>
                  ))}
                </ul>
              </div>
            )}

            {goal.resource_scope && goal.resource_scope.length > 0 && (
              <div>
                <span className="text-slate-500 font-medium flex items-center gap-1.5 mb-1.5">
                  <Layers className="w-3.5 h-3.5 text-blue-600" /> Resource Scope:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {goal.resource_scope.map((rs, idx) => (
                    <Badge key={idx} variant="info" size="sm">
                      {rs}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Card>

        {/* Temporal & Execution Parameters */}
        <Card className="p-5 space-y-4">
          <div className="flex items-center gap-2 border-b border-slate-100 pb-3">
            <Clock className="w-4 h-4 text-emerald-600" />
            <h2 className="text-sm font-semibold text-slate-900">
              Execution Parameters
            </h2>
          </div>

          <div className="space-y-3 text-xs">
            <div>
              <span className="text-slate-500 block mb-0.5">Canonical Goal ID</span>
              <span className="font-mono text-[11px] text-slate-800 select-all block break-all bg-slate-50 p-1.5 rounded border border-slate-200">
                {goal.id}
              </span>
            </div>

            <div>
              <span className="text-slate-500 block mb-0.5">Workspace Tenant</span>
              <Badge variant="neutral" size="sm">{goal.tenant_id}</Badge>
            </div>

            {goal.deadline && (
              <div>
                <span className="text-slate-500 block mb-0.5">Target Deadline</span>
                <div className="flex items-center gap-1.5 text-slate-800 font-medium">
                  <Calendar className="w-3.5 h-3.5 text-slate-400" />
                  {new Date(goal.deadline).toLocaleString()}
                </div>
              </div>
            )}

            {goal.target_window && (
              <div>
                <span className="text-slate-500 block mb-0.5">Target Window</span>
                <div className="text-[11px] space-y-0.5 text-slate-700 bg-slate-50 p-2 rounded border border-slate-200">
                  {goal.target_window.start_at && (
                    <div>Start: {formatDateTime(goal.target_window.start_at)}</div>
                  )}
                  {goal.target_window.end_at && (
                    <div>End: {formatDateTime(goal.target_window.end_at)}</div>
                  )}
                </div>
              </div>
            )}

            <div className="pt-2 border-t border-slate-100 text-[11px] text-slate-500 space-y-0.5">
              <div>Created: {formatDateTime(goal.created_at)}</div>
              <div>Updated: {formatDateTime(goal.updated_at)}</div>
            </div>
          </div>
        </Card>
      </div>

      {/* Prominent Linked Synthesized Plans Section */}
      <Card className="p-5 space-y-4">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <FileSpreadsheet className="w-4 h-4 text-indigo-600" />
            <h2 className="text-sm font-semibold text-slate-900">
              Synthesized Operational Plans ({plans.length})
            </h2>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={handleGeneratePlan}
            disabled={isGeneratingPlan}
            className="flex items-center gap-1.5"
          >
            <Sparkles className="w-3.5 h-3.5" />
            New Plan
          </Button>
        </div>

        {plans.length === 0 ? (
          <div className="p-8 text-center border border-dashed border-slate-200 rounded-lg text-xs text-slate-500 space-y-2 bg-slate-50/50">
            <p className="font-medium text-slate-700">No plans synthesized for this goal yet.</p>
            <p className="text-slate-500 text-[11px]">
              Click &quot;Generate Plan&quot; to synthesize an operational plan with WBS steps and dependency sequencing.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-slate-100 border border-slate-200 rounded-lg overflow-hidden bg-white">
            {plans.map((p) => (
              <div
                key={p.id}
                className="p-4 flex items-center justify-between hover:bg-slate-50/80 transition-colors"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs font-semibold text-slate-900">{p.plan_name}</span>
                    <Badge variant="neutral" size="sm">v{p.version_number}</Badge>
                    <Badge
                      variant={
                        p.status === "ACTIVE" || p.status === "APPROVED"
                          ? "success"
                          : p.status === "DRAFT"
                          ? "neutral"
                          : "warning"
                      }
                      size="sm"
                    >
                      {formatStatus(p.status)}
                    </Badge>
                    {p.is_baseline && (
                      <Badge variant="info" size="sm" dot>
                        Baselined
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-4 text-[11px] text-slate-500 font-mono flex-wrap">
                    <span>ID: {formatShortId(p.id)}</span>
                    {p.baseline_hash && (
                      <span className="flex items-center gap-1">
                        <Hash className="w-3 h-3 text-slate-400" />
                        {p.baseline_hash.substring(0, 10)}...
                      </span>
                    )}
                    <span>Synthesized {formatDate(p.created_at)}</span>
                  </div>
                </div>

                <Link
                  to={`/plans/${p.id}`}
                  className="inline-flex items-center gap-1.5 text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors no-underline"
                >
                  <span>Inspect WBS & Critical Path</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </Link>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
};
