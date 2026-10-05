import React, { useState } from "react";
import { WorkflowApiClient, WorkflowTask } from "../api/client";

interface TaskInboxProps {
  apiClient: WorkflowApiClient;
  tenantId: string;
}

const MOCK_INBOX_TASKS: WorkflowTask[] = [
  {
    id: "7b8d4f20-91a3-4819-a982-1d54ef98b101",
    tenant_id: "tenant-engineering",
    task_definition_key: "task_chair_approval",
    name: "Department Chair Review",
    description: "Review academic semester course scheduling draft",
    status: "PENDING",
    assignee: null,
    candidate_roles: ["department_chair", "academic_lead"],
    form_key: "form_exam_review",
    sla_status: "WARNING",
    warning_at: "2026-10-05T04:00:00Z",
    due_at: "2026-10-05T08:00:00Z",
    created_at: "2026-10-04T22:00:00Z",
  },
  {
    id: "9c3e2180-2a54-4f81-b271-9e812d4a5102",
    tenant_id: "tenant-engineering",
    task_definition_key: "task_dean_review",
    name: "Dean Final Sign-off",
    description: "Approve semester exam timetable and hall allocations",
    status: "CLAIMED",
    assignee: "dean_morris",
    candidate_roles: ["academic_dean"],
    form_key: "form_dean_signoff",
    sla_status: "RUNNING",
    warning_at: "2026-10-05T12:00:00Z",
    due_at: "2026-10-05T18:00:00Z",
    created_at: "2026-10-05T00:30:00Z",
  },
];

export const TaskInbox: React.FC<TaskInboxProps> = ({ apiClient, tenantId }) => {
  const [tasks, setTasks] = useState<WorkflowTask[]>(MOCK_INBOX_TASKS);
  const [selectedTask, setSelectedTask] = useState<WorkflowTask | null>(null);
  const [actionComment, setActionComment] = useState("");
  const [actionDecision, setActionDecision] = useState("APPROVE");
  const [actionStatus, setActionStatus] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const getSlaBadge = (status?: string | null) => {
    switch (status) {
      case "WARNING":
        return <span className="px-2 py-0.5 rounded text-xs bg-amber-950 text-amber-300 border border-amber-800">SLA Warning</span>;
      case "BREACHED":
        return <span className="px-2 py-0.5 rounded text-xs bg-rose-950 text-rose-300 border border-rose-800">SLA Breached</span>;
      case "PAUSED":
        return <span className="px-2 py-0.5 rounded text-xs bg-slate-800 text-slate-400 border border-slate-600">SLA Paused</span>;
      default:
        return <span className="px-2 py-0.5 rounded text-xs bg-emerald-950 text-emerald-300 border border-emerald-800">SLA On Track</span>;
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
    } catch (err: any) {
      setActionError(err.message || "Failed to submit task action");
      setActionStatus(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Top Banner */}
      <div className="p-4 bg-slate-800 rounded-lg border border-slate-700 flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-slate-100">Human Task Inbox</h3>
          <p className="text-xs text-slate-400">
            Tenant: <span className="font-mono text-blue-400">{tenantId}</span> | Canonical Task Authorization
          </p>
        </div>
        <span className="text-xs text-slate-400">
          Showing {tasks.length} Assigned / Pool Tasks
        </span>
      </div>

      {/* Task List */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="flex flex-col gap-2">
          {tasks.map((task) => (
            <div
              key={task.id}
              onClick={() => {
                setSelectedTask(task);
                setActionStatus(null);
                setActionError(null);
              }}
              className={`p-4 rounded-lg border cursor-pointer transition ${
                selectedTask?.id === task.id
                  ? "bg-slate-800 border-blue-500 shadow-md shadow-blue-500/10"
                  : "bg-slate-900 border-slate-800 hover:border-slate-700"
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-semibold text-slate-100">{task.name}</span>
                {getSlaBadge(task.sla_status)}
              </div>
              <p className="text-xs text-slate-400 mb-2">{task.description}</p>
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                <span className="font-mono">Task ID: {task.id.slice(0, 8)}...</span>
                <span>•</span>
                <span>Status: <strong className="text-slate-200">{task.status}</strong></span>
                <span>•</span>
                <span>Roles: {task.candidate_roles.join(", ")}</span>
              </div>
            </div>
          ))}
        </div>

        {/* Selected Task Detail & Action Submission */}
        {selectedTask ? (
          <div className="p-5 bg-slate-800 rounded-lg border border-slate-700 flex flex-col gap-4">
            <div className="border-b border-slate-700 pb-3">
              <h4 className="text-sm font-semibold text-slate-100">{selectedTask.name}</h4>
              <p className="text-xs text-slate-400 font-mono mt-0.5">Canonical Task ID: {selectedTask.id}</p>
              <p className="text-xs text-slate-400 mt-1">Form Mapping: {selectedTask.form_key || "None"}</p>
            </div>

            <div className="flex flex-col gap-3">
              <div>
                <label className="block text-xs text-slate-400 font-medium">Review Comment (Allowlisted)</label>
                <textarea
                  rows={3}
                  value={actionComment}
                  onChange={(e) => setActionComment(e.target.value)}
                  placeholder="Enter decision rationale..."
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
                />
              </div>

              <div>
                <label className="block text-xs text-slate-400 font-medium">Action Decision</label>
                <select
                  value={actionDecision}
                  onChange={(e) => setActionDecision(e.target.value)}
                  className="w-full px-3 py-1.5 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
                >
                  <option value="APPROVE">Approve</option>
                  <option value="REJECT">Reject</option>
                  <option value="REQUEST_CHANGES">Request Changes</option>
                </select>
              </div>

              {actionStatus && (
                <div className="p-3 bg-emerald-950/60 border border-emerald-800 rounded text-xs text-emerald-200">
                  {actionStatus}
                </div>
              )}

              {actionError && (
                <div className="p-3 bg-rose-950/60 border border-rose-800 rounded text-xs text-rose-200">
                  {actionError}
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2 pt-2">
                {selectedTask.status === "PENDING" && (
                  <button
                    onClick={() => handleExecuteAction("claim")}
                    className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-xs font-medium text-slate-200"
                  >
                    Claim Task
                  </button>
                )}
                {selectedTask.status === "CLAIMED" && (
                  <button
                    onClick={() => handleExecuteAction("unclaim")}
                    className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-xs font-medium text-slate-200"
                  >
                    Release (Unclaim)
                  </button>
                )}
                <button
                  onClick={() => handleExecuteAction("approve")}
                  className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded text-xs font-medium text-white"
                >
                  Submit Approval
                </button>
                <button
                  onClick={() => handleExecuteAction("reject")}
                  className="px-4 py-1.5 bg-rose-600 hover:bg-rose-500 rounded text-xs font-medium text-white"
                >
                  Submit Rejection
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="p-8 bg-slate-900 rounded-lg border border-slate-800 flex items-center justify-center text-slate-500 text-sm">
            Select a human task from the inbox to review and submit actions.
          </div>
        )}
      </div>
    </div>
  );
};
