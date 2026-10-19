import React, { useState, useEffect, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Target, Plus, Calendar, ShieldAlert, ArrowRight, RefreshCw, Layers } from "lucide-react";
import { apiClient } from "../api/client";
import type { GoalResponse, GoalCreate } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../components/ui/Toast";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { Modal } from "../components/ui/Modal";
import { Input } from "../components/ui/Input";
import { LoadingState } from "../components/ui/LoadingState";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";

export const GoalsPage: React.FC = () => {
  const { user } = useAuth();
  const { showToast } = useToast();
  const navigate = useNavigate();

  const [goals, setGoals] = useState<GoalResponse[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Create Goal Modal State
  const [isCreateOpen, setIsCreateOpen] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [objective, setObjective] = useState<string>("");
  const [scope, setScope] = useState<string>("");
  const [priorityTarget, setPriorityTarget] = useState<string>("");
  const [deadline, setDeadline] = useState<string>("");
  const [targetStart, setTargetStart] = useState<string>("");
  const [targetEnd, setTargetEnd] = useState<string>("");
  const [constraintsText, setConstraintsText] = useState<string>("");
  const [resourceScopeText, setResourceScopeText] = useState<string>("");

  const tenantId = user?.tenant_id || "tenant-engineering";

  const fetchGoals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiClient.listGoals(undefined, tenantId);
      setGoals(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load operational goals";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    fetchGoals();
  }, [fetchGoals]);

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!objective.trim()) {
      setCreateError("Objective is required.");
      return;
    }

    setIsSubmitting(true);
    setCreateError(null);

    try {
      const constraints = constraintsText
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const resourceScope = resourceScopeText
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);

      const payload: GoalCreate = {
        objective: objective.trim(),
        scope: scope.trim() || null,
        priority_target: priorityTarget.trim() || null,
        deadline: deadline ? new Date(deadline).toISOString() : null,
        target_window:
          targetStart || targetEnd
            ? {
                start_at: targetStart ? new Date(targetStart).toISOString() : null,
                end_at: targetEnd ? new Date(targetEnd).toISOString() : null,
              }
            : null,
        constraints: constraints.length > 0 ? constraints : undefined,
        resource_scope: resourceScope.length > 0 ? resourceScope : undefined,
      };

      const created = await apiClient.createGoal(payload, tenantId);
      showToast(
        "success",
        `Goal "${created.id.substring(0, 8)}..." established successfully.`,
        "Goal Created"
      );
      setIsCreateOpen(false);
      resetForm();
      navigate(`/goals/${created.id}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to create goal";
      setCreateError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const resetForm = () => {
    setObjective("");
    setScope("");
    setPriorityTarget("");
    setDeadline("");
    setTargetStart("");
    setTargetEnd("");
    setConstraintsText("");
    setResourceScopeText("");
    setCreateError(null);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Operational Goals"
        description="High-level operational objectives, planning constraints, and resource boundaries."
        actions={
          <div className="flex items-center gap-3">
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchGoals}
              disabled={loading}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => setIsCreateOpen(true)}
              className="flex items-center gap-1.5"
            >
              <Plus className="w-3.5 h-3.5" />
              Create Goal
            </Button>
          </div>
        }
      />

      {loading && <LoadingState message="Fetching operational goals from engine..." />}

      {error && !loading && (
        <ErrorState
          title="Failed to Load Goals"
          message={error}
          onRetry={fetchGoals}
        />
      )}

      {!loading && !error && goals.length === 0 && (
        <EmptyState
          title="No Operational Goals Found"
          description="Establish your first operational objective to trigger automated plan synthesis and WBS generation."
          actionLabel="Define New Goal"
          onAction={() => setIsCreateOpen(true)}
        />
      )}

      {!loading && !error && goals.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {goals.map((goal) => (
            <Card
              key={goal.id}
              className="p-5 flex flex-col justify-between hover:border-slate-700 transition-colors"
            >
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="p-1.5 rounded-lg bg-indigo-500/10 text-indigo-400">
                      <Target className="w-4 h-4" />
                    </span>
                    <span className="font-mono text-[11px] text-slate-400">
                      {goal.id.substring(0, 8)}...
                    </span>
                  </div>
                  {goal.priority_target && (
                    <Badge variant="warning">{goal.priority_target}</Badge>
                  )}
                </div>

                <div>
                  <h3 className="text-sm font-semibold text-white line-clamp-2 leading-snug">
                    {goal.objective}
                  </h3>
                  {goal.scope && (
                    <p className="text-xs text-slate-400 mt-1 line-clamp-2">{goal.scope}</p>
                  )}
                </div>

                <div className="space-y-1.5 text-xs text-slate-400 pt-2 border-t border-slate-800/80">
                  {goal.deadline && (
                    <div className="flex items-center gap-2">
                      <Calendar className="w-3.5 h-3.5 text-slate-400" />
                      <span>
                        Deadline:{" "}
                        <strong className="text-slate-200">
                          {new Date(goal.deadline).toLocaleDateString()}
                        </strong>
                      </span>
                    </div>
                  )}

                  {goal.constraints && goal.constraints.length > 0 && (
                    <div className="flex items-center gap-1.5 flex-wrap pt-1">
                      <ShieldAlert className="w-3.5 h-3.5 text-amber-400/80" />
                      <span className="text-[11px] text-slate-400">
                        {goal.constraints.length} constraint(s)
                      </span>
                    </div>
                  )}

                  {goal.resource_scope && goal.resource_scope.length > 0 && (
                    <div className="flex items-center gap-1.5 flex-wrap pt-1">
                      <Layers className="w-3.5 h-3.5 text-blue-400/80" />
                      <span className="text-[11px] text-slate-400">
                        Scope: {goal.resource_scope.join(", ")}
                      </span>
                    </div>
                  )}
                </div>
              </div>

              <div className="pt-4 mt-4 border-t border-slate-800 flex items-center justify-between text-xs">
                <span className="text-slate-400 text-[11px]">
                  Created {new Date(goal.created_at).toLocaleDateString()}
                </span>
                <Link
                  to={`/goals/${goal.id}`}
                  className="inline-flex items-center gap-1 text-indigo-400 hover:text-indigo-300 font-medium transition-colors"
                >
                  View Details <ArrowRight className="w-3.5 h-3.5" />
                </Link>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Create Goal Modal */}
      <Modal
        isOpen={isCreateOpen}
        onClose={() => {
          if (!isSubmitting) {
            setIsCreateOpen(false);
            resetForm();
          }
        }}
        title="Define Operational Goal"
      >
        <form onSubmit={handleCreateSubmit} className="space-y-4">
          {createError && (
            <div className="p-3 text-xs rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300">
              {createError}
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">
              Objective <span className="text-rose-400">*</span>
            </label>
            <textarea
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              placeholder="e.g. Conduct Semester 1 Final Examination for all engineering cohorts"
              rows={3}
              required
              minLength={3}
              maxLength={1000}
              className="w-full px-3 py-2 text-xs bg-slate-950 border border-slate-800 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
            <p className="text-[10px] text-slate-400 mt-1">
              High-level operational objective to achieve (3-1000 characters).
            </p>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">
              Operational Scope
            </label>
            <Input
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              placeholder="e.g. Campus-wide halls, invigilators, and laboratory assets"
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1">
                Priority Target
              </label>
              <Input
                value={priorityTarget}
                onChange={(e) => setPriorityTarget(e.target.value)}
                placeholder="e.g. HIGH, P0, CRITICAL"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1">
                Target Deadline
              </label>
              <Input
                type="datetime-local"
                value={deadline}
                onChange={(e) => setDeadline(e.target.value)}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1">
                Target Window Start
              </label>
              <Input
                type="datetime-local"
                value={targetStart}
                onChange={(e) => setTargetStart(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1">
                Target Window End
              </label>
              <Input
                type="datetime-local"
                value={targetEnd}
                onChange={(e) => setTargetEnd(e.target.value)}
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">
              Constraints (Comma-separated)
            </label>
            <Input
              value={constraintsText}
              onChange={(e) => setConstraintsText(e.target.value)}
              placeholder="e.g. No weekend sessions, Mandatory 15m transition buffer"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">
              Resource Scope Categories (Comma-separated)
            </label>
            <Input
              value={resourceScopeText}
              onChange={(e) => setResourceScopeText(e.target.value)}
              placeholder="e.g. ROOM, PERSON, EQUIPMENT"
            />
          </div>

          <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={isSubmitting}
              onClick={() => {
                setIsCreateOpen(false);
                resetForm();
              }}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              disabled={isSubmitting || !objective.trim()}
            >
              {isSubmitting ? "Creating..." : "Establish Goal"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
