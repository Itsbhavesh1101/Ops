import React, { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { WorkflowApiClient, WorkflowTask } from "../api/client";
import { CheckCircle2, RefreshCw } from "lucide-react";
import { Button } from "./ui/Button";

interface TaskInboxProps {
  apiClient: WorkflowApiClient;
  tenantId: string;
  initialTasks?: WorkflowTask[];
}

export const TaskInbox: React.FC<TaskInboxProps> = ({ apiClient, tenantId, initialTasks }) => {
  const [tasks, setTasks] = useState<WorkflowTask[]>(initialTasks || []);
  const [loading, setLoading] = useState<boolean>(!initialTasks);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<WorkflowTask | null>(null);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const liveTasks = await apiClient.listTasks(tenantId);
      setTasks(liveTasks);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load workflow tasks";
      setFetchError(msg);
    } finally {
      setLoading(false);
    }
  }, [apiClient, tenantId]);

  useEffect(() => {
    if (initialTasks !== undefined) return;
    loadTasks();
  }, [loadTasks, initialTasks]);

  const [actionComment, setActionComment] = useState("");
  const [actionDecision, setActionDecision] = useState("APPROVE");
  const [actionStatus, setActionStatus] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const getSlaBadge = (status?: string | null) => {
    switch (status) {
      case "WARNING":
        return <span className="px-2 py-0.5 rounded-full text-[11px] bg-amber-50 text-amber-700 border border-amber-200 font-medium">SLA Warning</span>;
      case "BREACHED":
        return <span className="px-2 py-0.5 rounded-full text-[11px] bg-rose-50 text-rose-700 border border-rose-200 font-medium">SLA Breached</span>;
      case "PAUSED":
        return <span className="px-2 py-0.5 rounded-full text-[11px] bg-slate-100 text-slate-600 border border-slate-200 font-medium">SLA Paused</span>;
      default:
        return <span className="px-2 py-0.5 rounded-full text-[11px] bg-emerald-50 text-emerald-700 border border-emerald-200 font-medium">SLA On Track</span>;
    }
  };

  const handleExecuteAction = async (action: "claim" | "unclaim" | "complete" | "approve" | "reject") => {
    if (!selectedTask) return;
    setActionStatus("Executing task action with Phase-1 Idempotency Key...");
    setActionError(null);

    // Generate canonical client idempotency key (UUIDv4)
    const idempotencyKey = `idemp_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;

    try {
      await apiClient.performTaskAction(
        tenantId,
        selectedTask.id,
        action,
        {
          comment: actionComment,
          decision: actionDecision,
          submitted_at: new Date().toISOString(),
        },
        idempotencyKey
      );
      setActionStatus(`Action '${action}' submitted successfully! (Idempotency-Key: ${idempotencyKey})`);
      // Update local task state
      setTasks((prev) =>
        prev.map((t) => (t.id === selectedTask.id ? { ...t, status: action === "claim" ? "CLAIMED" : "COMPLETED" } : t))
      );
      setSelectedTask((prev) =>
        prev && prev.id === selectedTask.id ? { ...prev, status: action === "claim" ? "CLAIMED" : "COMPLETED" } : prev
      );
    } catch (err: any) {
      setActionError(err.message || "Failed to submit task action");
      setActionStatus(null);
    }
  };

  return (
    <div className="space-y-6 text-left">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h2 className="text-xl sm:text-2xl font-semibold tracking-tight text-slate-900">
            Workflow Inbox
          </h2>
          <p className="text-xs sm:text-sm text-slate-500 mt-0.5">
            Human approval tasks, governance sign-offs, and verification workflows.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={loadTasks}
            disabled={loading}
            className="flex items-center gap-1.5"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {loading && !tasks.length && (
        <div className="p-4 bg-slate-50 border border-slate-200/80 rounded-xl text-xs text-slate-500">
          Loading workflow tasks from system of record...
        </div>
      )}

      {fetchError && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-xs text-rose-700">
          {fetchError}
        </div>
      )}

      {/* When 0 Tasks: Single Calm Empty State Card */}
      {!loading && !fetchError && tasks.length === 0 && (
        <div className="bg-white border border-slate-200/80 rounded-xl p-12 text-center max-w-lg mx-auto shadow-2xs space-y-4 my-8">
          <div className="w-12 h-12 rounded-full bg-slate-100 flex items-center justify-center text-slate-400 mx-auto">
            <CheckCircle2 className="w-6 h-6 text-emerald-600" />
          </div>
          <div className="text-center">
            <h3 className="text-base font-semibold text-slate-900 text-center">Human Task Inbox</h3>
            <p className="text-sm font-medium text-slate-700 mt-1 text-center">No tasks need your attention.</p>
            <p className="text-xs text-slate-500 mt-1 text-center">
              Approval and review tasks assigned to you will appear here.
            </p>
          </div>
          <div className="pt-2 flex flex-col items-center gap-3">
            <Button
              variant="secondary"
              size="sm"
              onClick={loadTasks}
              disabled={loading}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            <div className="text-[11px] text-slate-400 font-medium">
              0 Assigned • 0 Available
            </div>
          </div>
        </div>
      )}

      {/* When Tasks Exist: Two-Column Inbox & Action Detail Layout */}
      {tasks.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          <div className="flex flex-col gap-2.5">
            {tasks.map((task) => (
              <div
                key={task.id}
                onClick={() => {
                  setSelectedTask(task);
                  setActionStatus(null);
                  setActionError(null);
                }}
                className={`p-4 rounded-xl border cursor-pointer transition-all ${
                  selectedTask?.id === task.id
                    ? "bg-indigo-50/40 border-indigo-600 shadow-sm ring-1 ring-indigo-600/20"
                    : "bg-white border-slate-200/80 hover:border-slate-300 shadow-2xs"
                }`}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-sm font-semibold text-slate-900">{task.name || (task as any).task_name}</span>
                  {getSlaBadge(task.sla_status)}
                </div>
                <p className="text-xs text-slate-600 mb-2.5">{task.description}</p>
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <span className="font-mono text-[11px]">ID: {task.id.slice(0, 8)}...</span>
                  <span>•</span>
                  <span>Status: <strong className="text-slate-800">{task.status}</strong></span>
                  <span>•</span>
                  <span>Roles: {task.candidate_roles.join(", ")}</span>
                </div>
              </div>
            ))}
          </div>

          {/* Selected Task Detail & Action Submission */}
          {selectedTask ? (
            <div className="p-5 bg-white rounded-xl border border-slate-200/80 flex flex-col gap-4 shadow-2xs">
              <div className="border-b border-slate-100 pb-3">
                <h4 className="text-sm font-semibold text-slate-900">{selectedTask.name || (selectedTask as any).task_name}</h4>
                <p className="text-xs text-slate-500 font-mono mt-0.5">Task ID: {selectedTask.id}</p>
                {selectedTask.workflow_instance_id && (
                  <p className="text-xs mt-1">
                    <Link
                      to={`/workflows/${selectedTask.workflow_instance_id}`}
                      className="text-indigo-600 hover:text-indigo-800 font-mono underline inline-flex items-center gap-1"
                    >
                      Workflow Instance: {selectedTask.workflow_instance_id.slice(0, 8)}...
                    </Link>
                  </p>
                )}
                <p className="text-xs text-slate-500 mt-1">Form Mapping: {selectedTask.form_key || "None"}</p>
              </div>

              <div className="flex flex-col gap-3">
                <div>
                  <label className="block text-xs text-slate-700 font-medium mb-1">Review Comment (Allowlisted)</label>
                  <textarea
                    rows={3}
                    value={actionComment}
                    onChange={(e) => setActionComment(e.target.value)}
                    placeholder="Enter decision rationale..."
                    className="w-full px-3 py-2 bg-white border border-slate-300 rounded-lg text-xs text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-indigo-600"
                  />
                </div>

                <div>
                  <label className="block text-xs text-slate-700 font-medium mb-1">Action Decision</label>
                  <select
                    value={actionDecision}
                    onChange={(e) => setActionDecision(e.target.value)}
                    className="w-full px-3 py-2 bg-white border border-slate-300 rounded-lg text-xs text-slate-900 focus:outline-none focus:ring-1 focus:ring-indigo-600"
                  >
                    <option value="APPROVE">Approve</option>
                    <option value="REJECT">Reject</option>
                    <option value="REQUEST_CHANGES">Request Changes</option>
                  </select>
                </div>

                {actionStatus && (
                  <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-800">
                    {actionStatus}
                  </div>
                )}

                {actionError && (
                  <div className="p-3 bg-rose-50 border border-rose-200 rounded-lg text-xs text-rose-700">
                    {actionError}
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-slate-100">
                  {selectedTask.status === "PENDING" && (
                    <button
                      onClick={() => handleExecuteAction("claim")}
                      className="px-3 py-1.5 bg-white hover:bg-slate-50 border border-slate-300 rounded-lg text-xs font-medium text-slate-700 cursor-pointer"
                    >
                      Claim Task
                    </button>
                  )}
                  {selectedTask.status === "CLAIMED" && (
                    <button
                      onClick={() => handleExecuteAction("unclaim")}
                      className="px-3 py-1.5 bg-white hover:bg-slate-50 border border-slate-300 rounded-lg text-xs font-medium text-slate-700 cursor-pointer"
                    >
                      Release (Unclaim)
                    </button>
                  )}
                  <button
                    onClick={() => handleExecuteAction("approve")}
                    className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 rounded-lg text-xs font-semibold text-white cursor-pointer shadow-xs border-none"
                  >
                    Submit Approval
                  </button>
                  <button
                    onClick={() => handleExecuteAction("reject")}
                    className="px-4 py-1.5 bg-white hover:bg-rose-50 border border-rose-200 rounded-lg text-xs font-semibold text-rose-700 cursor-pointer"
                  >
                    Submit Rejection
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="p-8 bg-slate-50/70 rounded-xl border border-slate-200/80 flex items-center justify-center text-slate-500 text-xs">
              Select a human task from the inbox to review and submit actions.
            </div>
          )}
        </div>
      )}
    </div>
  );
};
