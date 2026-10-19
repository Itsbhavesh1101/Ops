import React, { useState, useEffect, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import {
  CheckCircle2,
  AlertTriangle,
  ArrowLeft,
  Clock,
  ShieldCheck,
  RefreshCw,
  GitCommit,
  Activity,
  Layers,
  Flag,
  AlertOctagon,
  Edit2,
  Check,
  Send,
} from "lucide-react";
import { apiClient, ApiError } from "../api/client";
import type {
  PlanDetailResponse,
  TaskDetailResponse,
  ValidationResult,
  CPMScheduleResult,
  TaskProgressStatus,
  TaskProgressUpdate,
} from "../api/types";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../components/ui/Toast";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { Modal } from "../components/ui/Modal";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Input } from "../components/ui/Input";
import { Select } from "../components/ui/Select";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "../components/ui/Table";
import { LoadingState } from "../components/ui/LoadingState";
import { ErrorState } from "../components/ui/ErrorState";

type PlanTab = "wbs" | "critical-path" | "milestones" | "dependencies" | "risks";

export const PlanDetailPage: React.FC = () => {
  const { planId } = useParams<{ planId: string }>();
  const { user } = useAuth();
  const { showToast } = useToast();

  const [plan, setPlan] = useState<PlanDetailResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [activeTab, setActiveTab] = useState<PlanTab>("wbs");

  // Plan Validation State
  const [isValidating, setIsValidating] = useState<boolean>(false);
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [isValidationModalOpen, setIsValidationModalOpen] = useState<boolean>(false);

  // Plan Submission State
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Plan Approval State
  const [isApproveConfirmOpen, setIsApproveConfirmOpen] = useState<boolean>(false);
  const [isApproving, setIsApproving] = useState<boolean>(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);

  // Critical Path Data
  const [cpmData, setCpmData] = useState<CPMScheduleResult | null>(null);
  const [cpmLoading, setCpmLoading] = useState<boolean>(false);
  const [cpmError, setCpmError] = useState<string | null>(null);

  // Task Progress Update Modal
  const [selectedTask, setSelectedTask] = useState<TaskDetailResponse | null>(null);
  const [isTaskModalOpen, setIsTaskModalOpen] = useState<boolean>(false);
  const [taskStatus, setTaskStatus] = useState<TaskProgressStatus>("TODO");
  const [taskProgress, setTaskProgress] = useState<number>(0);
  const [taskActualDuration, setTaskActualDuration] = useState<string>("");
  const [taskBlockedReason, setTaskBlockedReason] = useState<string>("");
  const [isUpdatingTask, setIsUpdatingTask] = useState<boolean>(false);
  const [taskUpdateError, setTaskUpdateError] = useState<string | null>(null);

  const tenantId = user?.tenant_id || "tenant-engineering";

  const canSubmit =
    user === null ||
    user.roles?.some((r) => ["planner", "operator", "tenant_admin", "admin"].includes(r)) ||
    user.permissions?.some((p) => ["plan:submit", "plan:edit", "*"].includes(p));

  const canApprove =
    user === null ||
    user.roles?.some((r) => ["approver", "tenant_admin", "admin"].includes(r)) ||
    user.permissions?.some((p) => ["plan:approve", "*"].includes(p));

  const fetchPlanDetail = useCallback(async () => {
    if (!planId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiClient.getPlan(planId, tenantId);
      setPlan(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load plan details";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [planId, tenantId]);

  const fetchCriticalPath = useCallback(async () => {
    if (!planId) return;
    setCpmLoading(true);
    setCpmError(null);
    try {
      const data = await apiClient.getCriticalPath(planId, tenantId);
      setCpmData(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load critical path calculation";
      setCpmError(msg);
    } finally {
      setCpmLoading(false);
    }
  }, [planId, tenantId]);

  useEffect(() => {
    fetchPlanDetail();
  }, [fetchPlanDetail]);

  useEffect(() => {
    if (activeTab === "critical-path" && !cpmData) {
      fetchCriticalPath();
    }
  }, [activeTab, cpmData, fetchCriticalPath]);

  // Validation Action
  const handleValidatePlan = async () => {
    if (!planId) return;
    setIsValidating(true);
    try {
      const result = await apiClient.validatePlan(planId, tenantId);
      setValidationResult(result);
      setIsValidationModalOpen(true);
      if (result.is_valid) {
        showToast(
          "success",
          "Plan structure verified successfully with zero cycle or dependency violations.",
          "Plan Validation Passed"
        );
        fetchPlanDetail();
      } else {
        showToast(
          "warning",
          `Plan contains ${result.issues?.length || 0} issue(s) that must be addressed.`,
          "Plan Validation Issues"
        );
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to validate plan";
      showToast("error", msg, "Validation Execution Error");
    } finally {
      setIsValidating(false);
    }
  };

  // Submit for Approval Action
  const handleSubmitForApproval = async () => {
    if (!planId) return;
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const updated = await apiClient.submitPlanForApproval(planId, tenantId);
      showToast(
        "success",
        `Plan "${updated.plan_name}" submitted for approval.`,
        "Submitted for Approval"
      );
      fetchPlanDetail();
    } catch (err: unknown) {
      let msg = "Failed to submit plan for approval";
      if (err instanceof ApiError && err.status === 403) {
        msg = "Permission Denied: User role lacks authority to submit plans for approval.";
      } else if (err instanceof ApiError && err.status === 422) {
        msg = `Plan State Violation: ${err.message || "Plan must be in VALIDATED state before submission."}`;
      } else if (err instanceof Error) {
        msg = err.message;
      }
      setSubmitError(msg);
      showToast("error", msg, "Submission Rejected");
    } finally {
      setIsSubmitting(false);
    }
  };

  // Approval Action
  const handleApprovePlan = async () => {
    if (!planId) return;
    setIsApproving(true);
    setApprovalError(null);
    try {
      const updated = await apiClient.approvePlan(planId, tenantId);
      showToast(
        "success",
        `Plan "${updated.plan_name}" approved with baseline status.`,
        "Plan Approved & Baselined"
      );
      setIsApproveConfirmOpen(false);
      fetchPlanDetail();
    } catch (err: unknown) {
      let msg = "Failed to approve plan";
      if (err instanceof ApiError && err.status === 403) {
        msg = "Permission Denied: User role lacks authority to approve and baseline operational plans.";
      } else if (err instanceof ApiError && err.status === 422) {
        msg = `Plan State Violation: ${err.message || "Plan must be in APPROVAL_PENDING state before approval."}`;
      } else if (err instanceof Error) {
        msg = err.message;
      }
      setApprovalError(msg);
      showToast("error", msg, "Approval Rejected");
    } finally {
      setIsApproving(false);
    }
  };

  // Open Task Progress Modal
  const openTaskModal = (task: TaskDetailResponse) => {
    setSelectedTask(task);
    setTaskStatus((task.status as TaskProgressStatus) || "TODO");
    setTaskProgress(task.progress_percentage || 0);
    setTaskActualDuration(task.actual_duration_minutes ? String(task.actual_duration_minutes) : "");
    setTaskBlockedReason(task.blocked_reason || "");
    setTaskUpdateError(null);
    setIsTaskModalOpen(true);
  };

  // Handle Task Progress Update Submit
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
      };

      await apiClient.updateTaskProgress(selectedTask.id, payload, tenantId);

      showToast(
        "success",
        `Task ${selectedTask.temporary_id} progress updated to ${taskProgress}%.`,
        "Task Progress Updated"
      );

      setIsTaskModalOpen(false);
      fetchPlanDetail();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update task progress";
      setTaskUpdateError(msg);
    } finally {
      setIsUpdatingTask(false);
    }
  };

  if (loading) {
    return <LoadingState message="Loading plan Work Breakdown Structure (WBS)..." />;
  }

  if (error || !plan) {
    return (
      <ErrorState
        title="Plan Unavailable"
        message={error || `Plan ID ${planId} could not be found.`}
        onRetry={fetchPlanDetail}
      />
    );
  }

  const tasks = plan.tasks || [];
  const milestones = plan.milestones || [];
  const dependencies = plan.dependencies || [];
  const risks = plan.risks || [];

  return (
    <div className="space-y-6">
      {/* Breadcrumb Navigation */}
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Link to="/plans" className="hover:text-slate-200 flex items-center gap-1">
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Plans
        </Link>
        <span>/</span>
        <span className="font-mono text-slate-300">{plan.id.substring(0, 8)}...</span>
      </div>

      {/* Header & Primary Actions */}
      <PageHeader
        title={plan.plan_name}
        description={`Plan Version v${plan.version_number} • Goal: ${plan.goal_id.substring(0, 8)}...`}
        actions={
          <div className="flex items-center gap-3">
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchPlanDetail}
              disabled={loading}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Refresh
            </Button>

            {(plan.status === "DRAFT" || plan.status === "GENERATED") && (
              <Button
                variant="primary"
                size="sm"
                onClick={handleValidatePlan}
                disabled={isValidating}
                className="flex items-center gap-1.5"
              >
                <ShieldCheck className={`w-3.5 h-3.5 ${isValidating ? "animate-spin" : ""}`} />
                {isValidating ? "Validating..." : "Validate Plan"}
              </Button>
            )}

            {plan.status === "VALIDATED" && (
              <>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleValidatePlan}
                  disabled={isValidating}
                  className="flex items-center gap-1.5 border-slate-700 hover:border-slate-600"
                >
                  <ShieldCheck className={`w-3.5 h-3.5 ${isValidating ? "animate-spin" : ""}`} />
                  {isValidating ? "Validating..." : "Re-validate"}
                </Button>

                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleSubmitForApproval}
                  disabled={isSubmitting || !canSubmit}
                  title={!canSubmit ? "Submission requires planner or tenant_admin role" : undefined}
                  className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white"
                >
                  <Send className={`w-3.5 h-3.5 ${isSubmitting ? "animate-spin" : ""}`} />
                  {isSubmitting ? "Submitting..." : "Submit for Approval"}
                </Button>
              </>
            )}

            {plan.status === "APPROVAL_PENDING" && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => setIsApproveConfirmOpen(true)}
                disabled={isApproving || !canApprove}
                title={!canApprove ? "Approval requires approver or tenant_admin role" : undefined}
                className="flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white"
              >
                <CheckCircle2 className="w-3.5 h-3.5" />
                {isApproving ? "Approving..." : "Approve Plan"}
              </Button>
            )}

            {(plan.status === "APPROVED" || plan.status === "ACTIVE" || plan.is_baseline) && (
              <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-emerald-950/60 border border-emerald-500/40 text-emerald-300">
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                Approved Baseline
              </div>
            )}
          </div>
        }
      />

      {submitError && (
        <div className="p-3.5 text-xs rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 flex items-center justify-between">
          <span>{submitError}</span>
          <button
            onClick={() => setSubmitError(null)}
            className="text-rose-400 hover:text-rose-200 font-bold"
          >
            ✕
          </button>
        </div>
      )}

      {approvalError && (
        <div className="p-3.5 text-xs rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 flex items-center justify-between">
          <span>{approvalError}</span>
          <button
            onClick={() => setApprovalError(null)}
            className="text-rose-400 hover:text-rose-200 font-bold"
          >
            ✕
          </button>
        </div>
      )}

      {/* Overview Stat Badges */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-400">Plan Status</span>
          <div>
            <Badge
              variant={
                plan.status === "APPROVED" || plan.status === "ACTIVE"
                  ? "success"
                  : plan.status === "DRAFT"
                  ? "neutral"
                  : "warning"
              }
            >
              {plan.status}
            </Badge>
          </div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-400">Baseline Status</span>
          <div>
            {plan.is_baseline ? (
              <Badge variant="info">BASELINED</Badge>
            ) : (
              <Badge variant="neutral">CANDIDATE</Badge>
            )}
          </div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-400">Total WBS Tasks</span>
          <div className="text-base font-bold text-white">{tasks.length}</div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-400">Milestones</span>
          <div className="text-base font-bold text-white">{milestones.length}</div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-400">Dependencies</span>
          <div className="text-base font-bold text-white">{dependencies.length}</div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-400">Identified Risks</span>
          <div className="text-base font-bold text-white">{risks.length}</div>
        </Card>
      </div>

      {/* Tabs */}
      <div className="border-b border-slate-800 flex gap-2">
        <button
          onClick={() => setActiveTab("wbs")}
          className={`px-4 py-2 text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
            activeTab === "wbs"
              ? "border-indigo-500 text-indigo-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Layers className="w-3.5 h-3.5" />
          Tasks / WBS ({tasks.length})
        </button>

        <button
          onClick={() => setActiveTab("critical-path")}
          className={`px-4 py-2 text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
            activeTab === "critical-path"
              ? "border-indigo-500 text-indigo-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Activity className="w-3.5 h-3.5 text-amber-400" />
          Critical Path Analysis
        </button>

        <button
          onClick={() => setActiveTab("milestones")}
          className={`px-4 py-2 text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
            activeTab === "milestones"
              ? "border-indigo-500 text-indigo-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Flag className="w-3.5 h-3.5 text-blue-400" />
          Milestones ({milestones.length})
        </button>

        <button
          onClick={() => setActiveTab("dependencies")}
          className={`px-4 py-2 text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
            activeTab === "dependencies"
              ? "border-indigo-500 text-indigo-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <GitCommit className="w-3.5 h-3.5 text-violet-400" />
          Dependencies ({dependencies.length})
        </button>

        <button
          onClick={() => setActiveTab("risks")}
          className={`px-4 py-2 text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
            activeTab === "risks"
              ? "border-indigo-500 text-indigo-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <AlertOctagon className="w-3.5 h-3.5 text-rose-400" />
          Risks ({risks.length})
        </button>
      </div>

      {/* Tab 1: Tasks / WBS Table */}
      {activeTab === "wbs" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Work Breakdown Structure tasks and execution progress.</span>
            <span>Click &quot;Update Progress&quot; to modify status or record actual execution duration.</span>
          </div>

          {tasks.length === 0 ? (
            <div className="p-8 text-center border border-dashed border-slate-800 rounded-xl text-xs text-slate-400">
              No tasks currently registered for this plan.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-20">Code / ID</TableHead>
                  <TableHead>Task Title & Description</TableHead>
                  <TableHead className="w-28">Status</TableHead>
                  <TableHead className="w-32">Progress</TableHead>
                  <TableHead className="w-24">Est. Dur.</TableHead>
                  <TableHead className="w-28">Role / Owner</TableHead>
                  <TableHead className="w-24 text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tasks.map((task) => (
                  <TableRow key={task.id}>
                    <TableCell className="font-mono text-[11px] font-semibold text-slate-300">
                      {task.temporary_id || task.wbs_code || task.id.substring(0, 6)}
                    </TableCell>

                    <TableCell>
                      <div className="font-semibold text-slate-100">{task.title}</div>
                      {task.description && (
                        <div className="text-[11px] text-slate-400 line-clamp-1">
                          {task.description}
                        </div>
                      )}
                      {task.blocked_reason && task.status === "BLOCKED" && (
                        <div className="mt-1 flex items-center gap-1 text-[11px] text-rose-400">
                          <AlertTriangle className="w-3 h-3" />
                          <span>Blocked: {task.blocked_reason}</span>
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
                        {task.status}
                      </Badge>
                    </TableCell>

                    <TableCell>
                      <div className="space-y-1">
                        <div className="flex justify-between text-[10px] text-slate-400">
                          <span>{task.progress_percentage}%</span>
                          {task.actual_duration_minutes !== null && task.actual_duration_minutes !== undefined && (
                            <span>{task.actual_duration_minutes}m actual</span>
                          )}
                        </div>
                        <div className="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                          <div
                            className={`h-full rounded-full ${
                              task.progress_percentage === 100
                                ? "bg-emerald-500"
                                : task.status === "BLOCKED"
                                ? "bg-rose-500"
                                : "bg-indigo-500"
                            }`}
                            style={{ width: `${task.progress_percentage}%` }}
                          />
                        </div>
                      </div>
                    </TableCell>

                    <TableCell className="text-slate-300">
                      {task.estimated_duration_minutes}m
                    </TableCell>

                    <TableCell className="text-[11px] text-slate-400">
                      {task.owner_role || "—"}
                    </TableCell>

                    <TableCell className="text-right">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => openTaskModal(task)}
                        className="text-[11px] py-1 px-2.5 h-auto flex items-center gap-1"
                      >
                        <Edit2 className="w-3 h-3" /> Update
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      )}

      {/* Tab 2: Critical Path Method */}
      {activeTab === "critical-path" && (
        <div className="space-y-5">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-white">
                Critical Path Method (CPM) Schedule Calculation
              </h3>
              <p className="text-xs text-slate-400">
                Tasks on the critical path have zero float (slack) and determine overall project completion.
              </p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchCriticalPath}
              disabled={cpmLoading}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${cpmLoading ? "animate-spin" : ""}`} />
              Recalculate CPM
            </Button>
          </div>

          {cpmLoading && <LoadingState message="Calculating critical path metrics..." />}

          {cpmError && (
            <ErrorState
              title="CPM Calculation Error"
              message={cpmError}
              onRetry={fetchCriticalPath}
            />
          )}

          {!cpmLoading && !cpmError && cpmData && (
            <div className="space-y-4">
              <Card className="p-4 bg-slate-900/60 border-slate-800 flex items-center justify-between flex-wrap gap-4">
                <div>
                  <span className="text-xs text-slate-400">Total Project Finish Duration</span>
                  <div className="text-xl font-bold text-amber-400">
                    {cpmData.project_duration_minutes} minutes ({Math.round(cpmData.project_duration_minutes / 60)} hours)
                  </div>
                </div>

                <div>
                  <span className="text-xs text-slate-400">Critical Tasks Count</span>
                  <div className="text-xl font-bold text-white">
                    {cpmData.critical_path.length} tasks
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-400">Sequence:</span>
                  <div className="flex items-center gap-1 flex-wrap">
                    {cpmData.critical_path.map((tid, idx) => (
                      <React.Fragment key={tid}>
                        <span className="px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-300 font-mono text-xs">
                          {tid}
                        </span>
                        {idx < cpmData.critical_path.length - 1 && (
                          <span className="text-slate-400">→</span>
                        )}
                      </React.Fragment>
                    ))}
                  </div>
                </div>
              </Card>

              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Task Identifier</TableHead>
                    <TableHead>Duration</TableHead>
                    <TableHead>Early Start / Finish</TableHead>
                    <TableHead>Late Start / Finish</TableHead>
                    <TableHead>Total Slack</TableHead>
                    <TableHead>Path Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.entries(cpmData.schedule).map(([tid, node]) => (
                    <TableRow
                      key={tid}
                      className={node.is_critical ? "bg-amber-500/5 hover:bg-amber-500/10" : ""}
                    >
                      <TableCell className="font-mono text-xs font-semibold text-slate-200">
                        {node.task_id}
                      </TableCell>
                      <TableCell>{node.duration_minutes}m</TableCell>
                      <TableCell className="font-mono text-xs text-slate-300">
                        {node.early_start}m → {node.early_finish}m
                      </TableCell>
                      <TableCell className="font-mono text-xs text-slate-300">
                        {node.late_start}m → {node.late_finish}m
                      </TableCell>
                      <TableCell>
                        <span
                          className={`font-mono font-semibold ${
                            node.total_slack === 0 ? "text-amber-400" : "text-emerald-400"
                          }`}
                        >
                          {node.total_slack}m float
                        </span>
                      </TableCell>
                      <TableCell>
                        {node.is_critical ? (
                          <Badge variant="warning">CRITICAL PATH</Badge>
                        ) : (
                          <Badge variant="neutral">NON-CRITICAL</Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      )}

      {/* Tab 3: Milestones */}
      {activeTab === "milestones" && (
        <div className="space-y-4">
          <p className="text-xs text-slate-400">
            Key operational milestones and checkpoint dates synthesized in this plan.
          </p>

          {milestones.length === 0 ? (
            <div className="p-8 text-center border border-dashed border-slate-800 rounded-xl text-xs text-slate-400">
              No milestones defined for this plan.
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {milestones.map((m) => (
                <Card key={m.id} className="p-4 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs text-indigo-400 font-semibold">
                      {m.temporary_id}
                    </span>
                    {m.target_date && (
                      <span className="text-[11px] text-slate-400 flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {new Date(m.target_date).toLocaleDateString()}
                      </span>
                    )}
                  </div>
                  <h4 className="text-sm font-semibold text-white">{m.name}</h4>
                  {m.description && (
                    <p className="text-xs text-slate-400">{m.description}</p>
                  )}
                </Card>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tab 4: Dependencies */}
      {activeTab === "dependencies" && (
        <div className="space-y-4">
          <p className="text-xs text-slate-400">
            Task precedence relationships, lag times, and sequencing constraints.
          </p>

          {dependencies.length === 0 ? (
            <div className="p-8 text-center border border-dashed border-slate-800 rounded-xl text-xs text-slate-400">
              No task dependencies registered.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Predecessor Task ID</TableHead>
                  <TableHead>Successor Task ID</TableHead>
                  <TableHead>Dependency Type</TableHead>
                  <TableHead>Lag Duration</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {dependencies.map((dep) => (
                  <TableRow key={dep.id}>
                    <TableCell className="font-mono text-xs text-slate-200">
                      {dep.predecessor_id}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-indigo-300">
                      {dep.successor_id}
                    </TableCell>
                    <TableCell>
                      <Badge variant="neutral">{dep.dependency_type || "FINISH_TO_START"}</Badge>
                    </TableCell>
                    <TableCell className="text-slate-300">{dep.lag_minutes}m</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      )}

      {/* Tab 5: Risks */}
      {activeTab === "risks" && (
        <div className="space-y-4">
          <p className="text-xs text-slate-400">
            Identified operational risks, bottleneck hazards, and evaluation status.
          </p>

          {risks.length === 0 ? (
            <div className="p-8 text-center border border-dashed border-slate-800 rounded-xl text-xs text-slate-400">
              No critical risks flagged for this plan.
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {risks.map((risk) => (
                <Card key={risk.id} className="p-4 space-y-2 border-slate-800">
                  <div className="flex items-center justify-between">
                    <Badge
                      variant={
                        risk.severity === "CRITICAL" || risk.severity === "HIGH"
                          ? "error"
                          : risk.severity === "MEDIUM"
                          ? "warning"
                          : "neutral"
                      }
                    >
                      {risk.severity} SEVERITY
                    </Badge>
                    <span className="text-[11px] text-slate-400 font-mono">
                      Category: {risk.category}
                    </span>
                  </div>
                  <p className="text-xs text-slate-200 font-medium">{risk.description}</p>
                  <div className="flex items-center justify-between pt-2 border-t border-slate-800/80 text-[11px] text-slate-400">
                    <span>Task ID: {risk.task_id.substring(0, 8)}...</span>
                    <span>{risk.is_evaluable ? "Evaluable" : "Informational"}</span>
                  </div>
                </Card>
              ))}
            </div>
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
            <div className="p-3 text-xs rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300">
              {taskUpdateError}
            </div>
          )}

          <div className="p-3 rounded-lg bg-slate-900 border border-slate-800 text-xs space-y-1">
            <div className="text-slate-400 font-mono">Task ID: {selectedTask?.id}</div>
            <div className="text-slate-300 font-medium">Estimated: {selectedTask?.estimated_duration_minutes} minutes</div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">
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
            <label className="block text-xs font-semibold text-slate-300 mb-1">
              Progress Percentage: {taskProgress}%
            </label>
            <input
              type="range"
              min="0"
              max="100"
              step="5"
              value={taskProgress}
              onChange={(e) => setTaskProgress(Number(e.target.value))}
              className="w-full accent-indigo-500"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">
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
              <label className="block text-xs font-semibold text-slate-300 mb-1">
                Blocked Reason <span className="text-rose-400">*</span>
              </label>
              <textarea
                value={taskBlockedReason}
                onChange={(e) => setTaskBlockedReason(e.target.value)}
                rows={2}
                required
                placeholder="Explain what is blocking task execution..."
                className="w-full px-3 py-2 text-xs bg-slate-950 border border-slate-800 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-rose-500"
              />
            </div>
          )}

          <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
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

      {/* Validation Result Modal */}
      <Modal
        isOpen={isValidationModalOpen}
        onClose={() => setIsValidationModalOpen(false)}
        title="Plan Validation Diagnostics"
      >
        <div className="space-y-4">
          <div
            className={`p-4 rounded-xl border flex items-center gap-3 ${
              validationResult?.is_valid
                ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                : "bg-rose-500/10 border-rose-500/30 text-rose-300"
            }`}
          >
            {validationResult?.is_valid ? (
              <Check className="w-5 h-5 text-emerald-400 flex-shrink-0" />
            ) : (
              <AlertTriangle className="w-5 h-5 text-rose-400 flex-shrink-0" />
            )}
            <div>
              <h4 className="font-semibold text-sm">
                {validationResult?.is_valid ? "Plan Validated Successfully" : "Validation Violations Detected"}
              </h4>
              <p className="text-xs mt-0.5 opacity-90">
                {validationResult?.is_valid
                  ? "All precedence dependencies are topologically acyclic and sound."
                  : "Plan contains cyclic or invalid dependencies that must be resolved before approval."}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="p-3 bg-slate-900 border border-slate-800 rounded-lg">
              <span className="text-slate-400 block mb-0.5">Evaluated Tasks</span>
              <span className="font-bold text-white text-sm">
                {validationResult?.evaluated_task_count ?? tasks.length}
              </span>
            </div>
            <div className="p-3 bg-slate-900 border border-slate-800 rounded-lg">
              <span className="text-slate-400 block mb-0.5">Evaluated Dependencies</span>
              <span className="font-bold text-white text-sm">
                {validationResult?.evaluated_dependency_count ?? dependencies.length}
              </span>
            </div>
          </div>

          {validationResult?.issues && validationResult.issues.length > 0 && (
            <div className="space-y-2">
              <span className="text-xs font-semibold text-slate-300">
                Issues ({validationResult.issues.length})
              </span>
              <div className="max-h-48 overflow-y-auto space-y-2">
                {validationResult.issues.map((issue, idx) => (
                  <div
                    key={idx}
                    className="p-3 bg-slate-950 border border-slate-800 rounded-lg text-xs space-y-1"
                  >
                    <div className="flex items-center justify-between">
                      <Badge
                        variant={
                          issue.severity === "CRITICAL" || issue.severity === "ERROR"
                            ? "error"
                            : "warning"
                        }
                      >
                        {issue.severity || "ERROR"}
                      </Badge>
                      <span className="font-mono text-[10px] text-slate-400">
                        {issue.code}
                      </span>
                    </div>
                    <div className="text-slate-200">{issue.message}</div>
                    <div className="text-[10px] text-slate-400 font-mono">Location: {issue.location}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="flex justify-end pt-3 border-t border-slate-800">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setIsValidationModalOpen(false)}
            >
              Close Diagnostics
            </Button>
          </div>
        </div>
      </Modal>

      {/* Plan Approval Confirmation Dialog */}
      <ConfirmDialog
        isOpen={isApproveConfirmOpen}
        onClose={() => setIsApproveConfirmOpen(false)}
        onConfirm={handleApprovePlan}
        title="Approve and Baseline Plan"
        message={`Are you sure you want to approve "${plan.plan_name}" (v${plan.version_number})? Approval freezes this version as the authoritative operational baseline for scheduling and execution.`}
        confirmLabel={isApproving ? "Approving..." : "Confirm Approval"}
        isLoading={isApproving}
      />
    </div>
  );
};
