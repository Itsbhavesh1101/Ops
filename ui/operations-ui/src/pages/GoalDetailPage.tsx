import React, { useState, useEffect, useCallback } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  Target,
  FileSpreadsheet,
  Calendar,
  Clock,
  ShieldAlert,
  Layers,
  ArrowLeft,
  ArrowRight,
  Sparkles,
  RefreshCw,
  Hash,
} from "lucide-react";
import { apiClient } from "../api/client";
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

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Link to="/goals" className="hover:text-slate-200 flex items-center gap-1">
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Goals
        </Link>
        <span>/</span>
        <span className="font-mono text-slate-300">{goal.id.substring(0, 8)}...</span>
      </div>

      <PageHeader
        title={goal.objective}
        description={goal.scope || "Operational goal metadata and synthesis controls."}
        actions={
          <div className="flex items-center gap-3">
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
              className="flex items-center gap-2 shadow-sm shadow-indigo-500/20"
            >
              <Sparkles className={`w-4 h-4 ${isGeneratingPlan ? "animate-spin" : ""}`} />
              {isGeneratingPlan ? "Synthesizing Plan..." : "Generate Plan"}
            </Button>
          </div>
        }
      />

      {generateError && (
        <div className="p-3.5 text-xs rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300">
          <p className="font-semibold mb-0.5">Plan Generation Error</p>
          <p>{generateError}</p>
        </div>
      )}

      {/* Goal Details Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        <Card className="p-5 md:col-span-2 space-y-4">
          <h2 className="text-sm font-semibold text-white flex items-center gap-2">
            <Target className="w-4 h-4 text-indigo-400" />
            Operational Objective & Boundaries
          </h2>

          <div className="space-y-3 text-xs">
            <div>
              <span className="text-slate-400 font-medium">Objective:</span>
              <p className="text-slate-100 text-sm mt-0.5 leading-relaxed font-medium">
                {goal.objective}
              </p>
            </div>

            {goal.scope && (
              <div>
                <span className="text-slate-400 font-medium">Scope:</span>
                <p className="text-slate-300 mt-0.5 leading-relaxed">{goal.scope}</p>
              </div>
            )}

            {goal.constraints && goal.constraints.length > 0 && (
              <div>
                <span className="text-slate-400 font-medium flex items-center gap-1.5 mb-1.5">
                  <ShieldAlert className="w-3.5 h-3.5 text-amber-400" /> Active Constraints:
                </span>
                <ul className="list-disc list-inside space-y-1 text-slate-300 pl-1">
                  {goal.constraints.map((c, idx) => (
                    <li key={idx}>{c}</li>
                  ))}
                </ul>
              </div>
            )}

            {goal.resource_scope && goal.resource_scope.length > 0 && (
              <div>
                <span className="text-slate-400 font-medium flex items-center gap-1.5 mb-1.5">
                  <Layers className="w-3.5 h-3.5 text-blue-400" /> Resource Scope:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {goal.resource_scope.map((rs, idx) => (
                    <Badge key={idx} variant="info">
                      {rs}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Card>

        {/* Temporal & Operational Metadata */}
        <Card className="p-5 space-y-4">
          <h2 className="text-sm font-semibold text-white flex items-center gap-2">
            <Clock className="w-4 h-4 text-emerald-400" />
            Execution Parameters
          </h2>

          <div className="space-y-3 text-xs">
            <div>
              <span className="text-slate-400 block mb-0.5">Canonical Goal ID</span>
              <span className="font-mono text-[11px] text-slate-200 select-all block break-all">
                {goal.id}
              </span>
            </div>

            <div>
              <span className="text-slate-400 block mb-0.5">Tenant</span>
              <Badge variant="neutral">{goal.tenant_id}</Badge>
            </div>

            {goal.priority_target && (
              <div>
                <span className="text-slate-400 block mb-0.5">Priority Target</span>
                <Badge variant="warning">{goal.priority_target}</Badge>
              </div>
            )}

            {goal.deadline && (
              <div>
                <span className="text-slate-400 block mb-0.5">Target Deadline</span>
                <div className="flex items-center gap-1.5 text-slate-200">
                  <Calendar className="w-3.5 h-3.5 text-slate-400" />
                  {new Date(goal.deadline).toLocaleString()}
                </div>
              </div>
            )}

            {goal.target_window && (
              <div>
                <span className="text-slate-400 block mb-0.5">Target Window</span>
                <div className="text-[11px] space-y-0.5 text-slate-300">
                  {goal.target_window.start_at && (
                    <div>Start: {new Date(goal.target_window.start_at).toLocaleString()}</div>
                  )}
                  {goal.target_window.end_at && (
                    <div>End: {new Date(goal.target_window.end_at).toLocaleString()}</div>
                  )}
                </div>
              </div>
            )}

            <div className="pt-2 border-t border-slate-800 text-[11px] text-slate-400 space-y-0.5">
              <div>Created: {new Date(goal.created_at).toLocaleString()}</div>
              <div>Updated: {new Date(goal.updated_at).toLocaleString()}</div>
            </div>
          </div>
        </Card>
      </div>

      {/* Related Synthesized Plans */}
      <Card className="p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-white flex items-center gap-2">
            <FileSpreadsheet className="w-4 h-4 text-indigo-400" />
            Synthesized Operational Plans ({plans.length})
          </h2>
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
          <div className="p-6 text-center border border-dashed border-slate-800 rounded-xl text-xs text-slate-400 space-y-2">
            <p>No plans synthesized for this goal yet.</p>
            <p className="text-slate-400 text-[11px]">
              Click &quot;Generate Plan&quot; to synthesize an operational plan with WBS steps and dependency sequencing.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-slate-800 border border-slate-800 rounded-xl overflow-hidden">
            {plans.map((p) => (
              <div
                key={p.id}
                className="p-4 flex items-center justify-between hover:bg-slate-900/60 transition-colors"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-white">{p.plan_name}</span>
                    <Badge variant="neutral">v{p.version_number}</Badge>
                    <Badge
                      variant={
                        p.status === "ACTIVE" || p.status === "APPROVED"
                          ? "success"
                          : p.status === "DRAFT"
                          ? "neutral"
                          : "warning"
                      }
                    >
                      {p.status}
                    </Badge>
                    {p.is_baseline && <Badge variant="info">BASELINE</Badge>}
                  </div>
                  <div className="flex items-center gap-4 text-[11px] text-slate-400 font-mono">
                    <span>ID: {p.id.substring(0, 8)}...</span>
                    {p.baseline_hash && (
                      <span className="flex items-center gap-1">
                        <Hash className="w-3 h-3 text-slate-400" />
                        {p.baseline_hash.substring(0, 10)}...
                      </span>
                    )}
                    <span>Synthesized {new Date(p.created_at).toLocaleString()}</span>
                  </div>
                </div>

                <Link
                  to={`/plans/${p.id}`}
                  className="inline-flex items-center gap-1.5 text-xs font-medium text-indigo-400 hover:text-indigo-300 transition-colors"
                >
                  Inspect WBS & Critical Path <ArrowRight className="w-3.5 h-3.5" />
                </Link>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
};
