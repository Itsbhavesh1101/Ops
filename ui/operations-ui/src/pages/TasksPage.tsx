import React, { useState, useEffect, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import { RefreshCw, AlertTriangle, Layers, Edit2, CheckCircle2 } from "lucide-react";
import { apiClient } from "../api/client";
import { formatStatus } from "../utils/formatters";
import type { PlanResponse, TaskDetailResponse, TaskProgressStatus, TaskProgressUpdate } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../components/ui/Toast";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Select } from "../components/ui/Select";
import { Modal } from "../components/ui/Modal";
import { Input } from "../components/ui/Input";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "../components/ui/Table";
import { LoadingState } from "../components/ui/LoadingState";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";

export const TasksPage: React.FC = () => {
  const { user } = useAuth();
  const { showToast } = useToast();
  const navigate = useNavigate();

  const [plans, setPlans] = useState<PlanResponse[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<string>("");
  const [tasks, setTasks] = useState<TaskDetailResponse[]>([]);
  const [loadingPlans, setLoadingPlans] = useState<boolean>(true);
  const [loadingTasks, setLoadingTasks] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<string>("ALL");

  // Task Progress Update Modal
  const [selectedTask, setSelectedTask] = useState<TaskDetailResponse | null>(null);
  const [isTaskModalOpen, setIsTaskModalOpen] = useState<boolean>(false);
  const [taskStatus, setTaskStatus] = useState<TaskProgressStatus>("TODO");
  const [taskProgress, setTaskProgress] = useState<number>(0);
  const [taskActualDuration, setTaskActualDuration] = useState<string>("");
  const [taskBlockedReason, setTaskBlockedReason] = useState<string>("");
  const [taskCompletionEvidence, setTaskCompletionEvidence] = useState<string>("");
  const [isUpdatingTask, setIsUpdatingTask] = useState<boolean>(false);
  const [taskUpdateError, setTaskUpdateError] = useState<string | null>(null);

  const tenantId = user?.tenant_id || "tenant-engineering";

  const fetchPlans = useCallback(async () => {
    setLoadingPlans(true);
    setError(null);
    try {
      const data = await apiClient.listPlans(undefined, tenantId);
      setPlans(data);
      if (data.length > 0 && !selectedPlanId) {
        setSelectedPlanId(data[0].id);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load plans";
      setError(msg);
    } finally {
      setLoadingPlans(false);
    }
  }, [tenantId, selectedPlanId]);

  const fetchTasks = useCallback(async (planId: string) => {
    if (!planId) return;
    setLoadingTasks(true);
    try {
      const data = await apiClient.getPlanTasks(planId, tenantId);
      setTasks(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load tasks for selected plan";
      setError(msg);
    } finally {
      setLoadingTasks(false);
    }
  }, [tenantId]);

  useEffect(() => {
    fetchPlans();
  }, [fetchPlans]);

  useEffect(() => {
    if (selectedPlanId) {
      fetchTasks(selectedPlanId);
    }
  }, [selectedPlanId, fetchTasks]);

  const openTaskModal = (task: TaskDetailResponse) => {
    setSelectedTask(task);
    setTaskStatus((task.status as TaskProgressStatus) || "TODO");
    setTaskProgress(task.progress_percentage || 0);
    setTaskActualDuration(task.actual_duration_minutes ? String(task.actual_duration_minutes) : "");
    setTaskBlockedReason(task.blocked_reason || "");
    setTaskCompletionEvidence(task.completion_evidence || "");
    setTaskUpdateError(null);
    setIsTaskModalOpen(true);
  };

  const handleTaskSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTask) return;

    setIsUpdatingTask(true);
    setTaskUpdateError(null);

    try {
      const payload: TaskProgressUpdate = {
        status: taskStatus,
        progress_percentage: Number(taskProgress),
        actual_duration_minutes: taskActualDuration ? Number(taskActualDuration) : null,
        blocked_reason: taskStatus === "BLOCKED" ? taskBlockedReason.trim() || "Blocked" : null,
        completion_evidence: taskCompletionEvidence.trim() || null,
      };

      await apiClient.updateTaskProgress(selectedTask.id, payload, tenantId);

      showToast(
        "success",
        `Task ${selectedTask.temporary_id} updated.`,
        "Task Progress Updated"
      );

      setIsTaskModalOpen(false);
      if (selectedPlanId) {
        fetchTasks(selectedPlanId);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update task progress";
      setTaskUpdateError(msg);
    } finally {
      setIsUpdatingTask(false);
    }
  };

  const filteredTasks = tasks.filter((t) => {
    if (statusFilter === "ALL") return true;
    return t.status === statusFilter;
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tasks (WBS)"
        description="Operational task tracking, status execution, and duration recording across active plans."
        actions={
          <div className="flex items-center gap-2.5">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                if (selectedPlanId) fetchTasks(selectedPlanId);
              }}
              disabled={loadingTasks}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loadingTasks ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          </div>
        }
      />

      {loadingPlans && <LoadingState message="Loading available operational plans..." />}

      {error && !loadingPlans && (
        <ErrorState
          title="Failed to Load WBS Tasks"
          message={error}
          onRetry={fetchPlans}
        />
      )}

      {!loadingPlans && !error && plans.length === 0 && (
        <EmptyState
          title="No Operational Plans Found"
          description="Tasks are synthesized inside operational plans. Create a goal and generate a plan first."
          actionLabel="Create Goal"
          onAction={() => navigate("/goals")}
        />
      )}

      {!loadingPlans && plans.length > 0 && (
        <div className="space-y-4">
          {/* Compact Plan Selector & Filter Toolbar */}
          <div className="flex flex-wrap items-center justify-between gap-3 py-1">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-slate-500 whitespace-nowrap">Plan:</span>
                <select
                  value={selectedPlanId}
                  onChange={(e) => setSelectedPlanId(e.target.value)}
                  className="text-xs h-8 px-2.5 bg-white border border-slate-200 rounded-md shadow-xs focus:outline-hidden focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 text-slate-700 max-w-xs"
                >
                  {plans.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.plan_name} (v{p.version_number})
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-slate-500 whitespace-nowrap">Status:</span>
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="text-xs h-8 px-2.5 bg-white border border-slate-200 rounded-md shadow-xs focus:outline-hidden focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 text-slate-700"
                >
                  <option value="ALL">All Statuses</option>
                  <option value="TODO">To Do</option>
                  <option value="IN_PROGRESS">In Progress</option>
                  <option value="BLOCKED">Blocked</option>
                  <option value="COMPLETED">Completed</option>
                </select>
              </div>
            </div>

            {selectedPlanId && (
              <Link to={`/plans/${selectedPlanId}`} className="no-underline">
                <Button variant="ghost" size="sm" className="text-xs text-indigo-600 hover:text-indigo-800 flex items-center gap-1.5 h-8">
                  <Layers className="w-3.5 h-3.5" />
                  View Plan
                </Button>
              </Link>
            )}
          </div>

          {loadingTasks && <LoadingState message="Loading WBS tasks for selected plan..." />}

          {!loadingTasks && filteredTasks.length === 0 && (
            <EmptyState
              title="No Tasks Found"
              description="No tasks match the selected plan or status filter."
            />
          )}

          {!loadingTasks && filteredTasks.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Task</TableHead>
                  <TableHead className="w-28">Status</TableHead>
                  <TableHead className="w-36">Progress</TableHead>
                  <TableHead className="w-24">Duration</TableHead>
                  <TableHead className="w-24">Variance</TableHead>
                  <TableHead className="w-32">Role</TableHead>
                  <TableHead className="w-20 text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredTasks.map((task) => (
                  <TableRow key={task.id} className="hover:bg-slate-50/70 transition-colors">
                    <TableCell>
                      <div className="font-medium text-slate-900">{task.title}</div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="font-mono text-[11px] text-slate-400">
                          {task.temporary_id || task.wbs_code || task.id.substring(0, 6)}
                        </span>
                        {task.blocked_reason && task.status === "BLOCKED" && (
                          <div className="flex items-center gap-1 text-[11px] text-rose-600 font-medium">
                            <AlertTriangle className="w-3 h-3" />
                            <span>Blocked: {task.blocked_reason}</span>
                          </div>
                        )}
                      </div>
                      {task.completion_evidence && (
                        <div className="flex items-center gap-1 text-[11px] text-emerald-600 font-medium mt-0.5">
                          <CheckCircle2 className="w-3 h-3 text-emerald-600 shrink-0" />
                          <span className="truncate max-w-xs" title={task.completion_evidence}>
                            Evidence: {task.completion_evidence}
                          </span>
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          task.status === "COMPLETED"
                            ? "success"
                            : task.status === "IN_PROGRESS"
                            ? "info"
                            : task.status === "BLOCKED"
                            ? "error"
                            : "neutral"
                        }
                      >
                        {formatStatus(task.status)}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="space-y-1">
                        <div className="flex justify-between text-[11px] text-slate-500 tabular-nums">
                          <span>{task.progress_percentage}%</span>
                          {task.actual_duration_minutes !== null && task.actual_duration_minutes !== undefined && (
                            <span>{task.actual_duration_minutes}m</span>
                          )}
                        </div>
                        <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all duration-300 ${
                              task.progress_percentage === 100
                                ? "bg-emerald-500"
                                : task.status === "BLOCKED"
                                ? "bg-rose-500"
                                : "bg-indigo-600"
                            }`}
                            style={{ width: `${task.progress_percentage}%` }}
                          />
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="text-slate-700 tabular-nums text-xs">
                      {task.estimated_duration_minutes}m
                    </TableCell>
                    <TableCell>
                      {task.variance_minutes !== null && task.variance_minutes !== undefined ? (
                        <span
                          className={`font-mono text-xs font-semibold ${
                            task.variance_minutes > 0
                              ? "text-rose-600"
                              : task.variance_minutes < 0
                              ? "text-emerald-600"
                              : "text-slate-500"
                          }`}
                        >
                          {task.variance_minutes > 0 ? `+${task.variance_minutes}m` : `${task.variance_minutes}m`}
                        </span>
                      ) : (
                        <span className="text-slate-400 text-xs">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-slate-500">
                      {task.owner_role || "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openTaskModal(task)}
                        className="text-xs text-slate-600 hover:text-indigo-600 py-1 px-2 h-7 flex items-center gap-1"
                      >
                        <Edit2 className="w-3 h-3" /> Edit
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      )}

      {/* Task Progress Update Modal */}
      <Modal
        isOpen={isTaskModalOpen}
        onClose={() => {
          if (!isUpdatingTask) setIsTaskModalOpen(false);
        }}
        title={`Update Task: ${selectedTask?.title || ""}`}
      >
        <form onSubmit={handleTaskSubmit} className="space-y-4">
          {taskUpdateError && (
            <div className="p-3 text-xs rounded-lg bg-rose-50 border border-rose-200 text-rose-700">
              {taskUpdateError}
            </div>
          )}

          <div className="p-3 rounded-lg bg-slate-50 border border-slate-200 text-xs space-y-1">
            <div className="text-slate-500 font-mono">Task ID: {selectedTask?.id}</div>
            <div className="text-slate-800 font-medium">Estimated: {selectedTask?.estimated_duration_minutes} minutes</div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1">
              Progress Status
            </label>
            <Select
              value={taskStatus}
              onChange={(e) => {
                const val = e.target.value as TaskProgressStatus;
                setTaskStatus(val);
                if (val === "COMPLETED") setTaskProgress(100);
                if (val === "TODO" && taskProgress === 100) setTaskProgress(0);
              }}
              options={[
                { value: "TODO", label: "TODO - Not Started" },
                { value: "IN_PROGRESS", label: "IN_PROGRESS - Executing" },
                { value: "BLOCKED", label: "BLOCKED - Execution Impeded" },
                { value: "COMPLETED", label: "COMPLETED - Finished" },
              ]}
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1">
              Progress Percentage: {taskProgress}%
            </label>
            <input
              type="range"
              role="slider"
              min="0"
              max="100"
              step="5"
              value={taskProgress}
              onChange={(e) => setTaskProgress(Number(e.target.value))}
              className="w-full accent-indigo-600 cursor-pointer"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1">
              Actual Duration (Minutes)
            </label>
            <Input
              type="number"
              min="0"
              value={taskActualDuration}
              onChange={(e) => setTaskActualDuration(e.target.value)}
              placeholder="e.g. 45"
            />
          </div>

          {taskStatus === "BLOCKED" && (
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Blocked Reason <span className="text-rose-600">*</span>
              </label>
              <textarea
                value={taskBlockedReason}
                onChange={(e) => setTaskBlockedReason(e.target.value)}
                rows={2}
                required
                placeholder="Explain what is blocking task execution..."
                className="w-full px-3 py-2 text-xs bg-white border border-slate-300 rounded-lg text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-rose-500"
              />
            </div>
          )}

          <div>
            <label htmlFor="task-completion-evidence" className="block text-xs font-semibold text-slate-700 mb-1">
              Completion Evidence / Verification Reference
            </label>
            <Input
              id="task-completion-evidence"
              type="text"
              value={taskCompletionEvidence}
              onChange={(e) => setTaskCompletionEvidence(e.target.value)}
              placeholder="e.g. PR #2, commit 8e79995, audit log ID, verification notes..."
            />
            <p className="text-[10px] text-slate-500 mt-1">
              Verifiable completion artifact, code commit reference, or test outcome sign-off.
            </p>
          </div>

          <div className="flex items-center justify-end gap-2.5 pt-3 border-t border-slate-100">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={isUpdatingTask}
              onClick={() => setIsTaskModalOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              disabled={isUpdatingTask}
            >
              {isUpdatingTask ? "Saving..." : "Save Progress"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
