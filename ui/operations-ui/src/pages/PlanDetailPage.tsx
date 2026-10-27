import React, { useState, useEffect, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import {
  CheckCircle2,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
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
  Sparkles,
} from "lucide-react";
import { apiClient, ApiError } from "../api/client";
import { formatStatus, formatShortId } from "../utils/formatters";
import type {
  PlanDetailResponse,
  TaskDetailResponse,
  ValidationResult,
  CPMScheduleResult,
  TaskProgressStatus,
  TaskProgressUpdate,
  MilestoneHealthResponse,
  TaskRiskResponse,
  PlanRecommendationsResponse,
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

type PlanTab = "wbs" | "recommendations" | "critical-path" | "milestones" | "dependencies" | "risks";

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
  const [taskCompletionEvidence, setTaskCompletionEvidence] = useState<string>("");
  const [isUpdatingTask, setIsUpdatingTask] = useState<boolean>(false);
  const [taskUpdateError, setTaskUpdateError] = useState<string | null>(null);

  // DQ-2 Task Intelligence Recommendations State
  const [recommendationsData, setRecommendationsData] = useState<PlanRecommendationsResponse | null>(null);
  const [recLoading, setRecLoading] = useState<boolean>(false);
  const [recError, setRecError] = useState<string | null>(null);
  const [recIncludeBlocked, setRecIncludeBlocked] = useState<boolean>(false);
  const [recLimit, setRecLimit] = useState<number>(10);

  // DQ-2 Task Intelligence Milestone Health State
  const [derivedMilestones, setDerivedMilestones] = useState<MilestoneHealthResponse[]>([]);
  const [milestonesLoading, setMilestonesLoading] = useState<boolean>(false);
  const [milestonesError, setMilestonesError] = useState<string | null>(null);

  // DQ-2 Task Intelligence Risks State
  const [derivedRisks, setDerivedRisks] = useState<TaskRiskResponse[]>([]);
  const [risksLoading, setRisksLoading] = useState<boolean>(false);
  const [risksError, setRisksError] = useState<string | null>(null);

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

  const fetchRecommendations = useCallback(async () => {
    if (!planId) return;
    setRecLoading(true);
    setRecError(null);
    try {
      const data = await apiClient.getPlanRecommendations(
        planId,
        {
          limit: recLimit,
          include_blocked: recIncludeBlocked,
        },
        tenantId
      );
      setRecommendationsData(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load task recommendations";
      setRecError(msg);
    } finally {
      setRecLoading(false);
    }
  }, [planId, recLimit, recIncludeBlocked, tenantId]);

  const fetchMilestones = useCallback(async () => {
    if (!planId) return;
    setMilestonesLoading(true);
    setMilestonesError(null);
    try {
      const data = await apiClient.getPlanMilestones(planId, tenantId);
      setDerivedMilestones(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load milestone operational health";
      setMilestonesError(msg);
    } finally {
      setMilestonesLoading(false);
    }
  }, [planId, tenantId]);

  const fetchRisks = useCallback(async () => {
    if (!planId) return;
    setRisksLoading(true);
    setRisksError(null);
    try {
      const data = await apiClient.getPlanRisks(planId, tenantId);
      setDerivedRisks(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load operational risks";
      setRisksError(msg);
    } finally {
      setRisksLoading(false);
    }
  }, [planId, tenantId]);

  useEffect(() => {
    fetchPlanDetail();
  }, [fetchPlanDetail]);

  useEffect(() => {
    if (activeTab === "critical-path" && !cpmData) {
      fetchCriticalPath();
    } else if (activeTab === "recommendations") {
      fetchRecommendations();
    } else if (activeTab === "milestones") {
      fetchMilestones();
    } else if (activeTab === "risks") {
      fetchRisks();
    }
  }, [activeTab, cpmData, fetchCriticalPath, fetchRecommendations, fetchMilestones, fetchRisks]);

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
    setTaskCompletionEvidence(task.completion_evidence || "");
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
        completion_evidence: taskCompletionEvidence.trim() || null,
      };

      await apiClient.updateTaskProgress(selectedTask.id, payload, tenantId);

      showToast(
        "success",
        `Task ${selectedTask.temporary_id} progress updated to ${taskProgress}%.`,
        "Task Progress Updated"
      );

      setIsTaskModalOpen(false);
      fetchPlanDetail();
      if (activeTab === "recommendations") fetchRecommendations();
      if (activeTab === "milestones") fetchMilestones();
      if (activeTab === "risks") fetchRisks();
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
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <Link to="/plans" className="hover:text-slate-800 flex items-center gap-1 transition-colors">
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Plans
        </Link>
        <span>/</span>
        <span className="font-mono text-slate-400">{formatShortId(plan.id)}</span>
      </div>

      {/* Header & Primary Actions */}
      <PageHeader
        title={plan.plan_name}
        description={`Plan Version v${plan.version_number} • Goal: ${formatShortId(plan.goal_id)}`}
        actions={
          <div className="flex items-center gap-2.5 flex-wrap">
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
                  className="flex items-center gap-1.5"
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
                  className="flex items-center gap-1.5"
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
                className="flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-700 text-white border-emerald-600"
              >
                <CheckCircle2 className="w-3.5 h-3.5" />
                {isApproving ? "Approving..." : "Approve Plan"}
              </Button>
            )}

            {(plan.status === "APPROVED" || plan.status === "ACTIVE" || plan.is_baseline) && (
              <>
                <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-emerald-50 border border-emerald-200 text-emerald-700">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
                  Approved Baseline
                </div>
                <Link to="/scheduling/jobs">
                  <Button
                    variant="primary"
                    size="sm"
                    className="flex items-center gap-1.5"
                  >
                    <span>Proceed to Scheduling</span>
                    <ArrowRight className="w-3.5 h-3.5" />
                  </Button>
                </Link>
              </>
            )}
          </div>
        }
      />

      {submitError && (
        <div className="p-3.5 text-xs rounded-xl bg-rose-50 border border-rose-200 text-rose-700 flex items-center justify-between">
          <span>{submitError}</span>
          <button
            onClick={() => setSubmitError(null)}
            className="text-rose-500 hover:text-rose-700 font-bold"
          >
            ✕
          </button>
        </div>
      )}

      {approvalError && (
        <div className="p-3.5 text-xs rounded-xl bg-rose-50 border border-rose-200 text-rose-700 flex items-center justify-between">
          <span>{approvalError}</span>
          <button
            onClick={() => setApprovalError(null)}
            className="text-rose-500 hover:text-rose-700 font-bold"
          >
            ✕
          </button>
        </div>
      )}

      {/* Overview Stat Badges */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-500">Plan Status</span>
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
              {formatStatus(plan.status)}
            </Badge>
          </div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-500">Baseline Status</span>
          <div>
            {plan.is_baseline ? (
              <Badge variant="info">Baselined</Badge>
            ) : (
              <Badge variant="neutral">Candidate</Badge>
            )}
          </div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-500">Total WBS Tasks</span>
          <div className="text-base font-semibold text-slate-900 tabular-nums">{tasks.length}</div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-500">Milestones</span>
          <div className="text-base font-semibold text-slate-900 tabular-nums">{milestones.length}</div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-500">Dependencies</span>
          <div className="text-base font-semibold text-slate-900 tabular-nums">{dependencies.length}</div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-500">Identified Risks</span>
          <div className="text-base font-semibold text-slate-900 tabular-nums">{risks.length}</div>
        </Card>
      </div>

      {/* Tabs */}
      <div className="border-b border-slate-200 flex gap-1">
        <button
          onClick={() => setActiveTab("wbs")}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5 cursor-pointer ${
            activeTab === "wbs"
              ? "border-indigo-600 text-indigo-600 font-semibold"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <Layers className="w-3.5 h-3.5" />
          Tasks / WBS ({tasks.length})
        </button>

        <button
          onClick={() => setActiveTab("recommendations")}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5 cursor-pointer ${
            activeTab === "recommendations"
              ? "border-indigo-600 text-indigo-600 font-semibold"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <Sparkles className="w-3.5 h-3.5 text-indigo-600" />
          Recommendations {recommendationsData ? `(${recommendationsData.recommendations.length})` : ""}
        </button>

        <button
          onClick={() => setActiveTab("critical-path")}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5 cursor-pointer ${
            activeTab === "critical-path"
              ? "border-indigo-600 text-indigo-600 font-semibold"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <Activity className="w-3.5 h-3.5 text-amber-500" />
          Critical Path Analysis
        </button>

        <button
          onClick={() => setActiveTab("milestones")}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5 cursor-pointer ${
            activeTab === "milestones"
              ? "border-indigo-600 text-indigo-600 font-semibold"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <Flag className="w-3.5 h-3.5 text-blue-500" />
          Milestones ({milestones.length})
        </button>

        <button
          onClick={() => setActiveTab("dependencies")}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5 cursor-pointer ${
            activeTab === "dependencies"
              ? "border-indigo-600 text-indigo-600 font-semibold"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <GitCommit className="w-3.5 h-3.5 text-violet-500" />
          Dependencies ({dependencies.length})
        </button>

        <button
          onClick={() => setActiveTab("risks")}
          className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5 cursor-pointer ${
            activeTab === "risks"
              ? "border-indigo-600 text-indigo-600 font-semibold"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <AlertOctagon className="w-3.5 h-3.5 text-rose-500" />
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
                  <TableHead className="w-24">Variance</TableHead>
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
                      {task.completion_evidence && (
                        <div className="mt-1 flex items-center gap-1 text-[11px] text-emerald-400">
                          <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                          <span className="font-mono truncate max-w-xs" title={task.completion_evidence}>
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

                    <TableCell>
                      {task.variance_minutes !== null && task.variance_minutes !== undefined ? (
                        <span
                          className={`font-mono text-xs font-semibold ${
                            task.variance_minutes > 0
                              ? "text-rose-400"
                              : task.variance_minutes < 0
                              ? "text-emerald-400"
                              : "text-slate-400"
                          }`}
                        >
                          {task.variance_minutes > 0 ? `+${task.variance_minutes}m` : `${task.variance_minutes}m`}
                        </span>
                      ) : (
                        <span className="text-slate-500 text-xs">—</span>
                      )}
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

      {/* Tab 2: Recommendations (DQ-2 Task Intelligence) */}
      {activeTab === "recommendations" && (
        <div className="space-y-4">
          {/* Advisory Intelligence Banner */}
          <div className="p-4 rounded-xl bg-indigo-50/80 border border-indigo-200 text-slate-800 flex items-start gap-3 shadow-2xs">
            <Sparkles className="w-5 h-5 text-indigo-600 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <h4 className="text-xs font-semibold text-indigo-950 uppercase tracking-wide">
                Advisory Next-Best-Task Intelligence (FR-TI-09)
              </h4>
              <p className="text-xs text-indigo-900/90 leading-relaxed">
                Recommendations are computed deterministically from DAG topological precedence, critical-path float, and execution momentum.
                These recommendations are <strong>strictly advisory</strong> and will never automatically mutate task or plan state. Human operational judgment is required.
              </p>
            </div>
          </div>

          {/* CPM Diagnostic Banner (if degraded) */}
          {recommendationsData?.cpm_diagnostic && (
            <div className="p-3.5 rounded-xl bg-amber-50 border border-amber-200 text-amber-900 text-xs flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
              <span>CPM Diagnostic: {recommendationsData.cpm_diagnostic}</span>
            </div>
          )}

          {/* Filter Toolbar & Heuristic Status */}
          <div className="flex flex-wrap items-center justify-between gap-3 p-3 bg-white border border-slate-200/80 rounded-xl shadow-2xs">
            <div className="flex flex-wrap items-center gap-4 text-xs">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={recIncludeBlocked}
                  onChange={(e) => setRecIncludeBlocked(e.target.checked)}
                  className="rounded text-indigo-600 focus:ring-indigo-500 h-4 w-4"
                />
                <span className="text-slate-700 font-medium">Include Blocked Tasks (with mitigation actions)</span>
              </label>

              <div className="flex items-center gap-1.5 text-slate-500">
                <span>Limit:</span>
                <select
                  value={recLimit}
                  onChange={(e) => setRecLimit(Number(e.target.value))}
                  className="px-2 py-1 text-xs bg-white border border-slate-300 rounded-md text-slate-800"
                >
                  <option value={5}>Top 5</option>
                  <option value={10}>Top 10</option>
                  <option value={20}>Top 20</option>
                  <option value={50}>Top 50</option>
                </select>
              </div>

              <Badge variant={recommendationsData?.policy_applied ? "info" : "neutral"} size="sm">
                {recommendationsData?.policy_applied ? "Tenant Policy Applied" : "Structural Ordering Heuristic"}
              </Badge>
            </div>

            <Button
              variant="secondary"
              size="sm"
              onClick={fetchRecommendations}
              disabled={recLoading}
              className="flex items-center gap-1.5 h-8 text-xs"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${recLoading ? "animate-spin" : ""}`} />
              Refresh Recommendations
            </Button>
          </div>

          {/* Counters Row */}
          {recommendationsData && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Card className="p-3">
                <span className="text-[11px] text-slate-500">Ready to Start</span>
                <div className="text-lg font-bold text-emerald-600 tabular-nums">
                  {recommendationsData.ready_tasks_count}
                </div>
              </Card>
              <Card className="p-3">
                <span className="text-[11px] text-slate-500">Blocked / Impeded</span>
                <div className="text-lg font-bold text-amber-600 tabular-nums">
                  {recommendationsData.blocked_tasks_count}
                </div>
              </Card>
              <Card className="p-3">
                <span className="text-[11px] text-slate-500">Completed</span>
                <div className="text-lg font-bold text-slate-600 tabular-nums">
                  {recommendationsData.completed_tasks_count}
                </div>
              </Card>
              <Card className="p-3">
                <span className="text-[11px] text-slate-500">Total Evaluated</span>
                <div className="text-lg font-bold text-slate-900 tabular-nums">
                  {recommendationsData.total_tasks}
                </div>
              </Card>
            </div>
          )}

          {recLoading && <LoadingState message="Computing deterministic task recommendations..." />}

          {recError && (
            <ErrorState
              title="Recommendations Unavailable"
              message={recError}
              onRetry={fetchRecommendations}
            />
          )}

          {!recLoading && !recError && recommendationsData && (
            <div className="space-y-3">
              {recommendationsData.recommendations.length === 0 ? (
                <div className="p-8 text-center border border-dashed border-slate-300 rounded-xl text-xs text-slate-500 bg-white">
                  No actionable recommendations matching current filters. All ready tasks may be completed or blocked.
                </div>
              ) : (
                recommendationsData.recommendations.map((rec) => {
                  const matchedTask = tasks.find((t) => t.id === rec.task_id);
                  return (
                    <Card
                      key={rec.task_id}
                      className={`p-4 transition-all border ${
                        rec.is_critical_path ? "border-amber-200 bg-amber-50/20" : "border-slate-200/80 bg-white"
                      }`}
                    >
                      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
                        <div className="space-y-1.5 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="px-2 py-0.5 rounded-md bg-indigo-50 text-indigo-700 font-mono text-xs font-bold border border-indigo-200">
                              #{rec.rank}
                            </span>

                            <span
                              className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${
                                rec.action === "START_NOW"
                                  ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                                  : rec.action === "CONTINUE"
                                  ? "bg-blue-50 text-blue-700 border-blue-200"
                                  : rec.action === "UNBLOCK_DEPENDENCY"
                                  ? "bg-amber-50 text-amber-700 border-amber-200"
                                  : "bg-purple-50 text-purple-700 border-purple-200"
                              }`}
                            >
                              {(rec.action || "RECOMMENDED").replace(/_/g, " ")}
                            </span>

                            <span className="font-mono text-xs text-slate-500 font-medium">
                              {rec.temporary_id}
                            </span>

                            {rec.is_critical_path && (
                              <Badge variant="warning" size="sm">
                                Critical Path
                              </Badge>
                            )}

                            {rec.total_slack_minutes !== null && rec.total_slack_minutes !== undefined && (
                              <span className="text-[11px] text-slate-500 font-mono">
                                {rec.total_slack_minutes}m slack
                              </span>
                            )}

                            <Badge
                              variant={
                                (rec.confidence || rec.quality) === "DEGRADED"
                                  ? "error"
                                  : (rec.confidence || rec.quality) === "MEDIUM"
                                  ? "warning"
                                  : "success"
                              }
                              size="sm"
                            >
                              {rec.confidence || rec.quality} Confidence
                            </Badge>

                            <span className="text-[11px] text-slate-400 font-mono">
                              Score: {rec.score.toFixed(3)}
                            </span>
                          </div>

                          <h4 className="text-sm font-semibold text-slate-900">{rec.title}</h4>

                          <p className="text-xs text-slate-600 bg-slate-50/70 p-2.5 rounded-lg border border-slate-200/60">
                            {rec.explanation}
                          </p>

                          {rec.blocked_reasons && rec.blocked_reasons.length > 0 && (
                            <div className="flex flex-wrap items-center gap-1.5 pt-1 text-xs text-rose-700">
                              <span className="font-semibold text-[11px]">Blockers:</span>
                              {rec.blocked_reasons.map((r, i) => (
                                <span
                                  key={i}
                                  className="px-2 py-0.5 rounded bg-rose-50 text-rose-700 border border-rose-200 text-[11px]"
                                >
                                  {r}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>

                        {matchedTask && (
                          <div className="shrink-0 pt-1">
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => openTaskModal(matchedTask)}
                              className="text-xs flex items-center gap-1 h-8"
                            >
                              <Edit2 className="w-3 h-3" /> Update Progress
                            </Button>
                          </div>
                        )}
                      </div>
                    </Card>
                  );
                })
              )}
            </div>
          )}
        </div>
      )}

      {/* Tab 3: Critical Path Method */}
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

      {/* Tab 4: Milestones with Derived Health (FR-TI-06) */}
      {activeTab === "milestones" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Operational milestones with derived task-completion health and schedule adherence.</span>
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchMilestones}
              disabled={milestonesLoading}
              className="flex items-center gap-1.5 h-8 text-xs"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${milestonesLoading ? "animate-spin" : ""}`} />
              Refresh Health
            </Button>
          </div>

          {milestonesLoading && <LoadingState message="Evaluating milestone operational health rollups..." />}

          {milestonesError && (
            <ErrorState
              title="Milestone Health Evaluation Error"
              message={milestonesError}
              onRetry={fetchMilestones}
            />
          )}

          {!milestonesLoading && !milestonesError && (
            <>
              {(derivedMilestones.length > 0 ? derivedMilestones : milestones).length === 0 ? (
                <div className="p-8 text-center border border-dashed border-slate-300 rounded-xl text-xs text-slate-500 bg-white">
                  No milestones defined for this operational plan.
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {(derivedMilestones.length > 0 ? derivedMilestones : milestones).map((m: any) => {
                    const health = m.health || "PENDING";
                    return (
                      <Card key={m.id} className="p-4 space-y-3 bg-white border border-slate-200/80 shadow-2xs">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-xs text-indigo-600 font-semibold">
                              {m.temporary_id}
                            </span>
                            <span
                              className={`px-2 py-0.5 rounded-full text-[11px] font-semibold border ${
                                health === "COMPLETED"
                                  ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                                  : health === "ON_TRACK"
                                  ? "bg-blue-50 text-blue-700 border-blue-200"
                                  : health === "AT_RISK"
                                  ? "bg-rose-50 text-rose-700 border-rose-200"
                                  : "bg-amber-50 text-amber-700 border-amber-200"
                              }`}
                            >
                              {health.replace("_", " ")}
                            </span>
                            {m.is_overdue && (
                              <span className="px-2 py-0.5 rounded bg-rose-100 text-rose-800 text-[10px] font-bold">
                                OVERDUE
                              </span>
                            )}
                          </div>
                          {m.target_date && (
                            <span className="text-[11px] text-slate-500 flex items-center gap-1">
                              <Clock className="w-3 h-3" />
                              {new Date(m.target_date).toLocaleDateString()}
                            </span>
                          )}
                        </div>

                        <div>
                          <h4 className="text-sm font-semibold text-slate-900">{m.name}</h4>
                          {m.description && <p className="text-xs text-slate-500 mt-0.5">{m.description}</p>}
                        </div>

                        {/* Progress and task counts if derived */}
                        {m.total_tasks !== undefined && (
                          <div className="space-y-1.5 pt-2 border-t border-slate-100 text-xs text-slate-600">
                            <div className="flex items-center justify-between text-[11px]">
                              <span>
                                {m.completed_tasks} / {m.total_tasks} tasks complete ({m.blocked_tasks} blocked)
                              </span>
                              <span className="font-semibold text-slate-900">
                                {m.progress_percentage?.toFixed(0) || 0}%
                              </span>
                            </div>
                            <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
                              <div
                                className={`h-full rounded-full transition-all ${
                                  health === "COMPLETED"
                                    ? "bg-emerald-500"
                                    : health === "AT_RISK"
                                    ? "bg-rose-500"
                                    : "bg-indigo-600"
                                }`}
                                style={{ width: `${Math.min(100, m.progress_percentage || 0)}%` }}
                              />
                            </div>
                          </div>
                        )}
                      </Card>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Tab 5: Dependencies */}
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
                      <Badge variant="neutral">{formatStatus(dep.dependency_type || "FINISH_TO_START")}</Badge>
                    </TableCell>
                    <TableCell className="text-slate-300">{dep.lag_minutes}m</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      )}

      {/* Tab 6: Risks (Operational Risk Signals) */}
      {activeTab === "risks" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Deterministic operational risk detection, cycle hazards, and execution blocker signals.</span>
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchRisks}
              disabled={risksLoading}
              className="flex items-center gap-1.5 h-8 text-xs"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${risksLoading ? "animate-spin" : ""}`} />
              Refresh Risks
            </Button>
          </div>

          {risksLoading && <LoadingState message="Scanning plan for operational risks and bottlenecks..." />}

          {risksError && (
            <ErrorState
              title="Risk Detection Unavailable"
              message={risksError}
              onRetry={fetchRisks}
            />
          )}

          {!risksLoading && !risksError && (
            <>
              {(derivedRisks.length > 0 ? derivedRisks : risks).length === 0 ? (
                <div className="p-8 text-center border border-dashed border-slate-300 rounded-xl text-xs text-slate-500 bg-white">
                  No critical risks or bottlenecks flagged for this operational plan.
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {(derivedRisks.length > 0 ? derivedRisks : risks).map((risk: any, idx: number) => (
                    <Card key={risk.id || idx} className="p-4 space-y-2 bg-white border border-slate-200/80 shadow-2xs">
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
                        <span className="text-[11px] text-slate-500 font-mono">
                          Category: {risk.category}
                        </span>
                      </div>
                      <p className="text-xs text-slate-800 font-medium leading-relaxed">{risk.description}</p>
                      <div className="flex items-center justify-between pt-2 border-t border-slate-100 text-[11px] text-slate-500">
                        <span>Task ID: {typeof risk.task_id === "string" ? risk.task_id.substring(0, 8) : ""}...</span>
                        <div className="flex items-center gap-2">
                          {risk.detected_at && (
                            <span>{new Date(risk.detected_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                          )}
                          <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 text-[10px]">
                            {risk.is_evaluable ? "Evaluable" : "Informational"}
                          </span>
                        </div>
                      </div>
                    </Card>
                  ))}
                </div>
              )}
            </>
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
            <label className="block text-xs font-semibold text-slate-700 mb-1">
              Completion Evidence / Verification Reference
            </label>
            <Input
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

      {/* Validation Result Modal */}
      <Modal
        isOpen={isValidationModalOpen}
        onClose={() => setIsValidationModalOpen(false)}
        title="Plan Validation Diagnostics"
      >
        <div className="space-y-4">
          <div
            className={`p-4 rounded-lg border flex items-center gap-3 ${
              validationResult?.is_valid
                ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                : "bg-rose-50 border-rose-200 text-rose-800"
            }`}
          >
            {validationResult?.is_valid ? (
              <Check className="w-5 h-5 text-emerald-600 shrink-0" />
            ) : (
              <AlertTriangle className="w-5 h-5 text-rose-600 shrink-0" />
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
            <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg">
              <span className="text-slate-500 block mb-0.5">Evaluated Tasks</span>
              <span className="font-bold text-slate-900 text-sm tabular-nums">
                {validationResult?.evaluated_task_count ?? tasks.length}
              </span>
            </div>
            <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg">
              <span className="text-slate-500 block mb-0.5">Evaluated Dependencies</span>
              <span className="font-bold text-slate-900 text-sm tabular-nums">
                {validationResult?.evaluated_dependency_count ?? dependencies.length}
              </span>
            </div>
          </div>

          {validationResult?.issues && validationResult.issues.length > 0 && (
            <div className="space-y-2">
              <span className="text-xs font-semibold text-slate-700">
                Issues ({validationResult.issues.length})
              </span>
              <div className="max-h-48 overflow-y-auto space-y-2">
                {validationResult.issues.map((issue, idx) => (
                  <div
                    key={idx}
                    className="p-3 bg-white border border-slate-200 rounded-lg text-xs space-y-1 shadow-xs"
                  >
                    <div className="flex items-center justify-between">
                      <Badge
                        variant={
                          issue.severity === "CRITICAL" || issue.severity === "ERROR"
                            ? "error"
                            : "warning"
                        }
                        size="sm"
                      >
                        {issue.severity || "ERROR"}
                      </Badge>
                      <span className="font-mono text-[10px] text-slate-400">
                        {issue.code}
                      </span>
                    </div>
                    <div className="text-slate-800">{issue.message}</div>
                    <div className="text-[10px] text-slate-400 font-mono">Location: {issue.location}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="flex justify-end pt-3 border-t border-slate-100">
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
