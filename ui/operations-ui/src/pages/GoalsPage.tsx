import React, { useState, useEffect, useCallback, useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Target, Plus, Calendar, ShieldAlert, ArrowRight, RefreshCw, Search } from "lucide-react";
import { apiClient } from "../api/client";
import { formatDate } from "../utils/formatters";
import type { GoalResponse, GoalCreate } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../components/ui/Toast";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Modal } from "../components/ui/Modal";
import { Input } from "../components/ui/Input";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "../components/ui/Table";
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
  const [searchQuery, setSearchQuery] = useState<string>("");

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

  const filteredGoals = useMemo(() => {
    if (!searchQuery.trim()) return goals;
    const q = searchQuery.toLowerCase();
    return goals.filter(
      (g) =>
        g.objective.toLowerCase().includes(q) ||
        (g.scope && g.scope.toLowerCase().includes(q)) ||
        (g.priority_target && g.priority_target.toLowerCase().includes(q))
    );
  }, [goals, searchQuery]);

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
          <div className="flex items-center gap-2.5">
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
        <div className="space-y-4">
          {/* Compact Filter Toolbar */}
          <div className="flex items-center justify-between gap-4 py-1">
            <div className="w-72">
              <Input
                placeholder="Search goals..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                leftIcon={<Search className="w-3.5 h-3.5" />}
              />
            </div>
            <div className="text-xs text-slate-500 tabular-nums">
              {filteredGoals.length} {filteredGoals.length === 1 ? "goal" : "goals"}
            </div>
          </div>

          {/* Operational Goals Table */}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Goal</TableHead>
                <TableHead>Priority</TableHead>
                <TableHead>Scope</TableHead>
                <TableHead>Deadline</TableHead>
                <TableHead>Status / Constraints</TableHead>
                <TableHead className="text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredGoals.map((goal) => (
                <TableRow key={goal.id} className="hover:bg-slate-50/70 transition-colors">
                  <TableCell>
                    <Link
                      to={`/goals/${goal.id}`}
                      className="font-medium text-slate-900 hover:text-indigo-600 transition-colors no-underline block"
                    >
                      {goal.objective}
                    </Link>
                  </TableCell>
                  <TableCell>
                    {goal.priority_target ? (
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
                    ) : (
                      <span className="text-slate-400 text-xs">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <span className="text-xs text-slate-600 line-clamp-1">
                      {goal.scope || "—"}
                    </span>
                  </TableCell>
                  <TableCell>
                    {goal.deadline ? (
                      <div className="flex items-center gap-1.5 text-xs text-slate-600">
                        <Calendar className="w-3.5 h-3.5 text-slate-400" />
                        <span>{formatDate(goal.deadline)}</span>
                      </div>
                    ) : (
                      <span className="text-slate-400 text-xs">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {goal.constraints && goal.constraints.length > 0 ? (
                      <div className="flex items-center gap-1.5 text-xs text-amber-700">
                        <ShieldAlert className="w-3.5 h-3.5 text-amber-500" />
                        <span>{goal.constraints.length} constraint{goal.constraints.length > 1 ? "s" : ""}</span>
                      </div>
                    ) : (
                      <span className="text-slate-400 text-xs">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <Link
                      to={`/goals/${goal.id}`}
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
        </div>
      )}

      {/* Create Goal Modal */}
      <Modal
        isOpen={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        title="Define Operational Goal"
        description="Specify operational objective and constraints for engine plan synthesis."
        maxWidth="lg"
      >
        <form onSubmit={handleCreateSubmit} className="space-y-4">
          {createError && (
            <div className="p-3 bg-rose-50 border border-rose-200 rounded-lg text-xs text-rose-700">
              {createError}
            </div>
          )}

          <Input
            label="Objective *"
            placeholder="e.g. Conduct Semester 1 Final Examination"
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
            required
          />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input
              label="Scope"
              placeholder="e.g. Campus-wide Faculty of Engineering"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
            />
            <Input
              label="Priority Target"
              placeholder="e.g. HIGH, NORMAL, CRITICAL"
              value={priorityTarget}
              onChange={(e) => setPriorityTarget(e.target.value)}
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <Input
              label="Hard Deadline"
              type="datetime-local"
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
            />
            <Input
              label="Target Window Start"
              type="datetime-local"
              value={targetStart}
              onChange={(e) => setTargetStart(e.target.value)}
            />
            <Input
              label="Target Window End"
              type="datetime-local"
              value={targetEnd}
              onChange={(e) => setTargetEnd(e.target.value)}
            />
          </div>

          <Input
            label="Operational Constraints (comma separated)"
            placeholder="e.g. NO_SUNDAY_EXAMS, MAX_CONCURRENT_2"
            value={constraintsText}
            onChange={(e) => setConstraintsText(e.target.value)}
          />

          <Input
            label="Resource Scope (comma separated)"
            placeholder="e.g. hall-alpha, hall-beta, invigilator-pool-1"
            value={resourceScopeText}
            onChange={(e) => setResourceScopeText(e.target.value)}
          />

          <div className="pt-4 border-t border-slate-100 flex items-center justify-end gap-2.5">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setIsCreateOpen(false)}
              disabled={isSubmitting}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              isLoading={isSubmitting}
              className="flex items-center gap-1.5"
            >
              <Target className="w-3.5 h-3.5" />
              Establish Goal
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
