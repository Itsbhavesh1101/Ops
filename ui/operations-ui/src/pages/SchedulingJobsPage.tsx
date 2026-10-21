import React, { useState, useEffect, useRef, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Cpu,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  Send,
  Zap,
  CalendarDays,
} from "lucide-react";
import { apiClient } from "../api/client";
import { formatStatus, formatDateTime, formatShortId } from "../utils/formatters";
import type {
  ScheduleJobResponse,
  ScheduleJobSubmitRequest,
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
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";

export const SchedulingJobsPage: React.FC = () => {
  const { user } = useAuth();
  const { showToast } = useToast();
  const navigate = useNavigate();

  const [activeJob, setActiveJob] = useState<ScheduleJobResponse | null>(null);
  const [resultingScheduleId, setResultingScheduleId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Submit Job Modal
  const [isSubmitModalOpen, setIsSubmitModalOpen] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [scheduleKey, setScheduleKey] = useState<string>("exam-schedule-default");
  const [solveSeconds, setSolveSeconds] = useState<number>(10);
  const [activityCount, setActivityCount] = useState<number>(3);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Publish Dialog
  const [isPublishOpen, setIsPublishOpen] = useState<boolean>(false);
  const [isPublishing, setIsPublishing] = useState<boolean>(false);

  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tenantId = user?.tenant_id || "tenant-engineering";

  const stopPolling = () => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  };

  const pollJobStatus = useCallback(
    async (jobId: string) => {
      try {
        const job = await apiClient.getScheduleJob(jobId, tenantId);
        setActiveJob(job);

        const status = job.status.toUpperCase();
        if (
          status === "FEASIBLE" ||
          status === "INFEASIBLE" ||
          status === "FAILED"
        ) {
          stopPolling();
          if (status === "FEASIBLE") {
            showToast(
              "success",
              `Job ${job.job_id.substring(0, 8)} solved successfully (FEASIBLE).`,
              "Optimal Schedule Found"
            );
            try {
              const scheds = await apiClient.listSchedules(undefined, tenantId);
              const found = scheds.find((s) => s.source_job_id === job.job_id);
              if (found) {
                setResultingScheduleId(found.id);
              }
            } catch {
              // Non-blocking schedule query
            }
          } else if (status === "INFEASIBLE") {
            showToast(
              "warning",
              "Constraint satisfaction solver proved problem is INFEASIBLE under current bounds.",
              "Schedule Infeasible"
            );
          }
        }
      } catch (err: unknown) {
        stopPolling();
        const msg = err instanceof Error ? err.message : "Polling failed";
        setError(msg);
      }
    },
    [tenantId, showToast]
  );

  useEffect(() => {
    const loadLatestJob = async () => {
      try {
        const scheds = await apiClient.listSchedules(undefined, tenantId);
        if (scheds && scheds.length > 0) {
          const sorted = [...scheds].sort((a, b) => b.version - a.version);
          const latest = sorted[0];
          setResultingScheduleId(latest.id);
          if (latest.source_job_id) {
            const job = await apiClient.getScheduleJob(latest.source_job_id, tenantId).catch(() => null);
            if (job) setActiveJob(job);
          }
        }
      } catch {
        // Non-blocking initial query
      }
    };
    loadLatestJob();
    return () => {
      stopPolling();
    };
  }, [tenantId]);

  const handleSubmitJob = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setSubmitError(null);
    setError(null);
    stopPolling();

    try {
      const now = new Date();
      let horizonStart = new Date(now.getTime() + 3600 * 1000).toISOString();
      let horizonEnd = new Date(now.getTime() + 3600 * 1000 * 8).toISOString();

      // Check registered availability windows to align operational horizon
      try {
        const availabilities = await apiClient.listAvailability(undefined, tenantId);
        if (availabilities && availabilities.length > 0) {
          const openWindow = availabilities.find((a) => a.interval_type === "AVAILABLE");
          if (openWindow) {
            horizonStart = openWindow.start_at;
            horizonEnd = openWindow.end_at;
          }
        }
      } catch {
        // Non-blocking fallback
      }

      // Query real canonical resources registered for this tenant in PostgreSQL
      const registeredResources = await apiClient.listResources(undefined, tenantId).catch(() => []);
      if (!registeredResources || registeredResources.length === 0) {
        setSubmitError("No resources registered for this tenant. Please register rooms and staff in the Resources catalog before submitting a solver job.");
        setIsSubmitting(false);
        return;
      }

      const schedulingResources = registeredResources.slice(0, 5).map((r) => ({
        id: r.id,
        type: r.type,
        display_label: r.display_label,
        capacity: r.type === "ROOM" ? 1 : (r.capacity || 1),
      }));
      const requiredResourceIds = schedulingResources.slice(0, 2).map((r) => r.id);

      // Query approved plan tasks to schedule legitimate operational tasks with precedence
      let taskActivities: Array<{
        id: string;
        title: string;
        duration_minutes: number;
        allowed_windows?: Array<{ start_at: string; end_at: string }>;
        required_resource_ids: string[];
        predecessors?: string[];
      }> = [];
      try {
        const plans = await apiClient.listPlans(undefined, tenantId);
        const approvedPlan = plans.find((p) => p.status === "APPROVED");
        if (approvedPlan) {
          const planDetail = await apiClient.getPlan(approvedPlan.id, tenantId).catch(() => null);
          const tasks = planDetail?.tasks?.length ? planDetail.tasks : await apiClient.getPlanTasks(approvedPlan.id, tenantId);
          const deps = planDetail?.dependencies || [];

          if (tasks && tasks.length > 0) {
            const taskIdToTempId = new Map<string, string>();
            tasks.forEach((t) => taskIdToTempId.set(t.id, t.temporary_id || t.id));

            const selectedTasks = tasks.slice(0, Math.min(tasks.length, activityCount));
            const selectedIds = new Set(selectedTasks.map((t) => t.temporary_id || t.id));

            taskActivities = selectedTasks.map((t) => {
              const myTempId = t.temporary_id || t.id;
              // Map direct predecessors from canonical plan dependencies
              const directPreds = deps
                .filter((d) => d.successor_id === t.id)
                .map((d) => taskIdToTempId.get(d.predecessor_id) || d.predecessor_id)
                .filter((predId) => selectedIds.has(predId));

              return {
                id: myTempId,
                title: t.title,
                duration_minutes: Math.max(15, t.estimated_duration_minutes || 60),
                allowed_windows: [{ start_at: horizonStart, end_at: horizonEnd }],
                required_resource_ids: requiredResourceIds,
                predecessors: directPreds.length > 0 ? directPreds : undefined,
              };
            });
          }
        }
      } catch {
        // Fallback to operational sessions if plan tasks cannot be fetched
      }

      const activities =
        taskActivities.length > 0
          ? taskActivities
          : Array.from({ length: activityCount }, (_, i) => ({
              id: `session-${i + 1}`,
              title: `Session ${i + 1}: Operational Activity`,
              duration_minutes: 60,
              allowed_windows: [{ start_at: horizonStart, end_at: horizonEnd }],
              required_resource_ids: requiredResourceIds,
              predecessors: i > 0 ? [`session-${i}`] : undefined,
            }));

      // Synthesize clean canonical problem definition using real tenant resources
      const payload: ScheduleJobSubmitRequest = {
        schedule_key: scheduleKey.trim() || "exam-schedule-default",
        request: {
          schedule_key: scheduleKey.trim() || "exam-schedule-default",
          horizon_start: horizonStart,
          horizon_end: horizonEnd,
          timezone: "UTC",
          profile: {
            profile_key: "standard-cpsat",
            version: 1,
            name: "Standard CP-SAT Profile",
            horizon_start: horizonStart,
            horizon_end: horizonEnd,
            time_granularity_minutes: 15,
            max_solve_seconds: solveSeconds,
            num_search_workers: 1,
            soft_weights: {
              preference_weight: 1,
              idle_gap_weight: 1,
              fairness_weight: 1,
              utilization_weight: 1,
              movement_weight: 1,
            },
          },
          resources: schedulingResources,
          activities: activities,
          constraints: [],
        },
      };

      const response = await apiClient.createScheduleJob(payload, tenantId);
      setActiveJob(response);
      setIsSubmitModalOpen(false);

      showToast(
        "info",
        `Job ${response.job_id.substring(0, 8)} queued in solver worker.`,
        "Scheduling Job Started"
      );

      // Begin polling
      pollingRef.current = setInterval(() => {
        pollJobStatus(response.job_id);
      }, 2000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to submit scheduling job";
      setSubmitError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handlePublishJob = async () => {
    if (!activeJob) return;
    setIsPublishing(true);
    try {
      const pubResp = await apiClient.publishScheduleJob(
        activeJob.job_id,
        { principal: user?.principal_id || "operator" },
        tenantId
      );
      setIsPublishOpen(false);
      showToast(
        "success",
        `Schedule ${pubResp.schedule_id.substring(0, 8)} published (v${pubResp.version}).`,
        "Schedule Published"
      );
      navigate(`/schedules/${pubResp.schedule_id}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to publish schedule";
      showToast("error", msg, "Publication Error");
    } finally {
      setIsPublishing(false);
    }
  };

  const getStatusBadge = (status: string) => {
    const s = status.toUpperCase();
    switch (s) {
      case "FEASIBLE":
        return <Badge variant="success">FEASIBLE</Badge>;
      case "INFEASIBLE":
        return <Badge variant="warning">INFEASIBLE</Badge>;
      case "FAILED":
        return <Badge variant="error">FAILED</Badge>;
      case "CLAIMED":
      case "SOLVING":
        return <Badge variant="info">SOLVING</Badge>;
      case "QUEUED":
        return <Badge variant="neutral">QUEUED</Badge>;
      default:
        return <Badge variant="neutral">{status}</Badge>;
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Scheduling Jobs"
        description="Generate and review operational schedules while respecting resource constraints."
        actions={
          <div className="flex items-center gap-3">
            <Link to="/schedules">
              <Button variant="secondary" size="sm">
                View Schedules
              </Button>
            </Link>
            {activeJob && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => setIsSubmitModalOpen(true)}
                className="flex items-center gap-1.5"
              >
                <Zap className="w-3.5 h-3.5" />
                Run Scheduler
              </Button>
            )}
          </div>
        }
      />

      {error && (
        <ErrorState
          title="Scheduling Engine Communication Error"
          message={error}
          onRetry={() => {
            if (activeJob) pollJobStatus(activeJob.job_id);
          }}
        />
      )}

      {/* Active / Current Job Display */}
      {activeJob ? (
        <div className="space-y-5">
          <Card className="p-5 space-y-4 bg-white border border-slate-200 rounded-xl shadow-xs">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="space-y-1">
                <div className="flex items-center gap-2.5">
                  <span className="p-1.5 rounded-lg bg-indigo-50 text-indigo-600">
                    <Cpu className="w-4 h-4" />
                  </span>
                  <h3 className="text-sm font-semibold text-slate-900">
                    Job ID: <span className="font-mono text-indigo-600">{activeJob.job_id}</span>
                  </h3>
                  {getStatusBadge(activeJob.status)}
                </div>
                <div className="text-xs text-slate-500 font-mono">
                  Schedule Key: <strong className="text-slate-700">{activeJob.schedule_key}</strong> • Hash:{" "}
                  {formatShortId(activeJob.input_snapshot_hash, 12)}
                </div>
              </div>

              <div className="flex items-center gap-2 flex-wrap">
                {activeJob.status.toUpperCase() === "FEASIBLE" && (
                  <>
                    <Link to={resultingScheduleId ? `/schedules/${resultingScheduleId}` : "/schedules"}>
                      <Button
                        variant="primary"
                        size="sm"
                        className="flex items-center gap-1.5"
                      >
                        <CalendarDays className="w-3.5 h-3.5" />
                        <span>Open Timetable</span>
                        <ArrowRight className="w-3.5 h-3.5" />
                      </Button>
                    </Link>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setIsPublishOpen(true)}
                      className="flex items-center gap-1.5 text-emerald-700 border-emerald-200 bg-emerald-50 hover:bg-emerald-100"
                    >
                      <Send className="w-3.5 h-3.5" />
                      Publish Schedule
                    </Button>
                  </>
                )}
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => pollJobStatus(activeJob.job_id)}
                  className="flex items-center gap-1"
                >
                  <RefreshCw className="w-3.5 h-3.5" /> Refresh
                </Button>
              </div>
            </div>

            {/* Timestamps & Progress */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs pt-3 border-t border-slate-100">
              <div>
                <span className="text-slate-500 block mb-0.5">Dispatched At</span>
                <span className="text-slate-800 font-mono tabular-nums">
                  {formatDateTime(activeJob.created_at)}
                </span>
              </div>
              <div>
                <span className="text-slate-500 block mb-0.5">Completed At</span>
                <span className="text-slate-800 font-mono tabular-nums">
                  {activeJob.completed_at
                    ? formatDateTime(activeJob.completed_at)
                    : "In Execution..."}
                </span>
              </div>
              <div>
                <span className="text-slate-500 block mb-0.5">Solver Strategy</span>
                <span className="text-slate-800 font-medium">OR-Tools CP-SAT</span>
              </div>
              <div>
                <span className="text-slate-500 block mb-0.5">Solver Outcome</span>
                <span className="font-semibold text-slate-900">{formatStatus(activeJob.status)}</span>
              </div>
            </div>

            {/* Infeasible Diagnostics Display */}
            {activeJob.status.toUpperCase() === "INFEASIBLE" && (
              <div className="p-4 rounded-xl bg-amber-50 border border-amber-200 text-xs space-y-2">
                <div className="flex items-center gap-2 text-amber-900 font-semibold text-sm">
                  <AlertTriangle className="w-4 h-4 text-amber-600" />
                  Solver Proved Problem INFEASIBLE
                </div>
                <p className="text-amber-800 leading-relaxed">
                  No valid timetable assignment exists that satisfies all hard constraints simultaneously.
                  The solver exhausted the search space without finding a conflict-free solution.
                </p>
                {activeJob.diagnostics && activeJob.diagnostics.length > 0 && (
                  <div className="space-y-1 pt-2 border-t border-amber-200 font-mono text-[11px]">
                    <span className="text-amber-900 font-semibold block">Solver Diagnostics:</span>
                    <pre className="p-3 bg-white border border-amber-200 rounded-lg text-amber-900 overflow-x-auto shadow-2xs">
                      {JSON.stringify(activeJob.diagnostics, null, 2)}
                    </pre>
                  </div>
                )}
                <div className="text-[11px] text-amber-700 pt-1">
                  💡 Recommendation: Increase the planning horizon window, add room/staff capacity, or relax strict non-overlap constraints.
                </div>
              </div>
            )}

            {/* Feasible Outcome Banner */}
            {activeJob.status.toUpperCase() === "FEASIBLE" && (
              <div className="p-4 rounded-xl bg-emerald-50 border border-emerald-200 text-xs space-y-2">
                <div className="flex items-center gap-2 text-emerald-900 font-semibold text-sm">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                  Feasible Timetable Generated Successfully
                </div>
                <p className="text-emerald-800">
                  Zero hard constraint violations detected. All activities have been assigned to conflict-free time slots and valid resources.
                </p>
                <div className="pt-2 flex items-center gap-3">
                  <Link
                    to={resultingScheduleId ? `/schedules/${resultingScheduleId}` : "/schedules"}
                    className="inline-flex items-center gap-1.5 font-semibold text-emerald-700 hover:text-emerald-800 underline"
                  >
                    View Timetable Assignment Matrix <ArrowRight className="w-3.5 h-3.5" />
                  </Link>
                </div>
              </div>
            )}
          </Card>
        </div>
      ) : (
        <EmptyState
          title="No scheduling jobs yet"
          description="Run the scheduler after an approved plan and resources are ready."
          actionLabel="Run Scheduler"
          actionAriaLabel="Dispatch First Job"
          onAction={() => setIsSubmitModalOpen(true)}
        />
      )}

      {/* Submit Job Modal */}
      <Modal
        isOpen={isSubmitModalOpen}
        onClose={() => {
          if (!isSubmitting) setIsSubmitModalOpen(false);
        }}
        title="Run Scheduler"
      >
        <div className="sr-only">Dispatch Mathematical Solver Job</div>
        <form onSubmit={handleSubmitJob} className="space-y-4">
          {submitError && (
            <div className="p-3 text-xs rounded-lg bg-rose-50 border border-rose-200 text-rose-700">
              {submitError}
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1">
              Schedule Key
            </label>
            <Input
              value={scheduleKey}
              onChange={(e) => setScheduleKey(e.target.value)}
              placeholder="e.g. sem1-finals-hall-alpha"
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Max Solve Time (Sec)
              </label>
              <Input
                type="number"
                min="1"
                max="60"
                value={solveSeconds}
                onChange={(e) => setSolveSeconds(Number(e.target.value))}
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Activity Sessions
              </label>
              <Input
                type="number"
                min="1"
                max="10"
                value={activityCount}
                onChange={(e) => setActivityCount(Number(e.target.value))}
              />
            </div>
          </div>

          <div className="p-3 rounded-lg bg-slate-50 border border-slate-200 text-xs space-y-1">
            <span className="font-semibold text-slate-800 block">Solver Configuration:</span>
            <ul className="list-disc list-inside space-y-0.5 text-slate-600 text-[11px]">
              <li>Engine: Google OR-Tools CP-SAT v9.8+</li>
              <li>Discretization: 15-minute intervals</li>
              <li>Resource Allocation: Non-overlapping sequential tasks</li>
            </ul>
          </div>

          <div className="flex items-center justify-end gap-2.5 pt-3 border-t border-slate-100">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={isSubmitting}
              onClick={() => setIsSubmitModalOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              disabled={isSubmitting}
              className="flex items-center gap-1.5"
              aria-label="Run CP-SAT Solver"
            >
              <Zap className="w-3.5 h-3.5" />
              <span>{isSubmitting ? "Starting..." : "Run Scheduler"}</span>
              <span className="sr-only">Run CP-SAT Solver</span>
            </Button>
          </div>
        </form>
      </Modal>

      {/* Publish Confirmation Dialog */}
      <ConfirmDialog
        isOpen={isPublishOpen}
        onClose={() => setIsPublishOpen(false)}
        onConfirm={handlePublishJob}
        title="Publish Canonical Schedule"
        message="Are you sure you want to publish this schedule? Publication transitions the timetable from candidate status to PUBLISHED, creating an authoritative baseline."
        confirmLabel={isPublishing ? "Publishing..." : "Confirm Publication"}
        isLoading={isPublishing}
      />
    </div>
  );
};
