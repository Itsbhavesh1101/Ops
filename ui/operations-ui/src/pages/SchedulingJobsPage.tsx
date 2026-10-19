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
} from "lucide-react";
import { apiClient } from "../api/client";
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
    return () => {
      stopPolling();
    };
  }, []);

  const handleSubmitJob = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setSubmitError(null);
    setError(null);
    stopPolling();

    try {
      const now = new Date();
      const horizonStart = new Date(now.getTime() + 3600 * 1000).toISOString();
      const horizonEnd = new Date(now.getTime() + 3600 * 1000 * 8).toISOString();

      // Synthesize clean canonical problem definition
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
          resources: [
            {
              id: "hall-alpha",
              type: "ROOM",
              display_label: "Examination Hall Alpha (Cap: 120)",
              capacity: 1,
            },
            {
              id: "invigilator-lead",
              type: "PERSON",
              display_label: "Senior Invigilator (Staff #1)",
              capacity: 1,
            },
          ],
          activities: Array.from({ length: activityCount }, (_, i) => ({
            id: `exam-session-${i + 1}`,
            title: `Session ${i + 1}: Engineering Cohort Examination`,
            duration_minutes: 60,
            required_resource_ids: ["hall-alpha", "invigilator-lead"],
          })),
          constraints: [],
        },
      };

      const response = await apiClient.createScheduleJob(payload, tenantId);
      setActiveJob(response);
      setIsSubmitModalOpen(false);

      showToast(
        "info",
        `Job ${response.job_id.substring(0, 8)} queued in solver worker.`,
        "Solver Job Dispatched"
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
        title="Scheduling Engine Jobs"
        description="Asynchronous CP-SAT mathematical solver orchestration, constraint verification, and timetable generation."
        actions={
          <div className="flex items-center gap-3">
            <Link to="/schedules">
              <Button variant="secondary" size="sm">
                View Timetables
              </Button>
            </Link>
            <Button
              variant="primary"
              size="sm"
              onClick={() => setIsSubmitModalOpen(true)}
              className="flex items-center gap-1.5"
            >
              <Zap className="w-3.5 h-3.5" />
              Dispatch Solver Job
            </Button>
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
          <Card className="p-5 space-y-4 border-slate-700">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="space-y-1">
                <div className="flex items-center gap-2.5">
                  <span className="p-1.5 rounded-lg bg-indigo-500/10 text-indigo-400">
                    <Cpu className="w-4 h-4" />
                  </span>
                  <h3 className="text-sm font-semibold text-white">
                    Job ID: <span className="font-mono text-indigo-300">{activeJob.job_id}</span>
                  </h3>
                  {getStatusBadge(activeJob.status)}
                </div>
                <div className="text-xs text-slate-400 font-mono">
                  Schedule Key: <strong className="text-slate-200">{activeJob.schedule_key}</strong> • Hash:{" "}
                  {activeJob.input_snapshot_hash.substring(0, 12)}...
                </div>
              </div>

              <div className="flex items-center gap-2">
                {activeJob.status.toUpperCase() === "FEASIBLE" && (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => setIsPublishOpen(true)}
                    className="flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white"
                  >
                    <Send className="w-3.5 h-3.5" />
                    Publish Schedule
                  </Button>
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
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs pt-3 border-t border-slate-800">
              <div>
                <span className="text-slate-400 block mb-0.5">Dispatched At</span>
                <span className="text-slate-200 font-mono">
                  {new Date(activeJob.created_at).toLocaleTimeString()}
                </span>
              </div>
              <div>
                <span className="text-slate-400 block mb-0.5">Completed At</span>
                <span className="text-slate-200 font-mono">
                  {activeJob.completed_at
                    ? new Date(activeJob.completed_at).toLocaleTimeString()
                    : "In Execution..."}
                </span>
              </div>
              <div>
                <span className="text-slate-400 block mb-0.5">Solver Strategy</span>
                <span className="text-slate-200">OR-Tools CP-SAT</span>
              </div>
              <div>
                <span className="text-slate-400 block mb-0.5">Solver Outcome</span>
                <span className="font-semibold text-slate-100">{activeJob.status}</span>
              </div>
            </div>

            {/* Infeasible Diagnostics Display */}
            {activeJob.status.toUpperCase() === "INFEASIBLE" && (
              <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs space-y-2">
                <div className="flex items-center gap-2 text-amber-300 font-semibold text-sm">
                  <AlertTriangle className="w-4 h-4 text-amber-400" />
                  Solver Proved Problem INFEASIBLE
                </div>
                <p className="text-amber-200/90 leading-relaxed">
                  No valid timetable assignment exists that satisfies all hard constraints simultaneously.
                  The solver exhausted the search space without finding a conflict-free solution.
                </p>
                {activeJob.diagnostics && activeJob.diagnostics.length > 0 && (
                  <div className="space-y-1 pt-2 border-t border-amber-500/20 font-mono text-[11px]">
                    <span className="text-amber-300 font-bold block">Solver Diagnostics:</span>
                    <pre className="p-2.5 bg-black/40 rounded-lg text-amber-200 overflow-x-auto">
                      {JSON.stringify(activeJob.diagnostics, null, 2)}
                    </pre>
                  </div>
                )}
                <div className="text-[11px] text-amber-300/80 pt-1">
                  💡 Recommendation: Increase the planning horizon window, add room/staff capacity, or relax strict non-overlap constraints.
                </div>
              </div>
            )}

            {/* Feasible Outcome Banner */}
            {activeJob.status.toUpperCase() === "FEASIBLE" && (
              <div className="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-xs space-y-2">
                <div className="flex items-center gap-2 text-emerald-300 font-semibold text-sm">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  Feasible Timetable Generated Successfully
                </div>
                <p className="text-emerald-200/90">
                  Zero hard constraint violations detected. All activities have been assigned to conflict-free time slots and valid resources.
                </p>
                <div className="pt-2 flex items-center gap-3">
                  <Link
                    to="/schedules"
                    className="inline-flex items-center gap-1.5 font-semibold text-emerald-400 hover:text-emerald-300 underline"
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
          title="No Active Solver Jobs"
          description="Dispatch a scheduling job to trigger the CP-SAT engine to solve activity-resource-time constraints."
          actionLabel="Dispatch First Job"
          onAction={() => setIsSubmitModalOpen(true)}
        />
      )}

      {/* Submit Job Modal */}
      <Modal
        isOpen={isSubmitModalOpen}
        onClose={() => {
          if (!isSubmitting) setIsSubmitModalOpen(false);
        }}
        title="Dispatch Mathematical Solver Job"
      >
        <form onSubmit={handleSubmitJob} className="space-y-4">
          {submitError && (
            <div className="p-3 text-xs rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300">
              {submitError}
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">
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
              <label className="block text-xs font-semibold text-slate-300 mb-1">
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
              <label className="block text-xs font-semibold text-slate-300 mb-1">
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

          <div className="p-3 rounded-lg bg-slate-900 border border-slate-800 text-xs space-y-1">
            <span className="font-semibold text-slate-300 block">Solver Pipeline Parameters:</span>
            <ul className="list-disc list-inside space-y-0.5 text-slate-400 text-[11px]">
              <li>Engine: Google OR-Tools CP-SAT v9.8+</li>
              <li>Workers: 1 deterministic search thread</li>
              <li>Discretization: 15-minute time quanta</li>
              <li>Resources: Examination Hall Alpha + Senior Invigilator</li>
            </ul>
          </div>

          <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
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
            >
              <Zap className="w-3.5 h-3.5" />
              {isSubmitting ? "Dispatching..." : "Run CP-SAT Solver"}
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
