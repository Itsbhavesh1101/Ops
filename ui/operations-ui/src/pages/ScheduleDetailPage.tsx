import React, { useState, useEffect, useCallback } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  CalendarDays,
  Clock,
  CheckCircle2,
  Lock,
  Unlock,
  Wrench,
  Send,
  ArrowLeft,
  ArrowRight,
  RefreshCw,
  LayoutGrid,
  List,
} from "lucide-react";
import { apiClient, ApiError } from "../api/client";
import type {
  ScheduleDetailResponse,
  ScheduleAssignmentItem,
  ScheduleRepairRequest,
  ScheduleRepairResponse,
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
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "../components/ui/Table";
import { LoadingState } from "../components/ui/LoadingState";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";

export const ScheduleDetailPage: React.FC = () => {
  const { scheduleId } = useParams<{ scheduleId: string }>();
  const { user } = useAuth();
  const { showToast } = useToast();
  const navigate = useNavigate();

  const [schedule, setSchedule] = useState<ScheduleDetailResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [viewMode, setViewMode] = useState<"grid" | "table">("grid");

  // Publish State
  const [isPublishConfirmOpen, setIsPublishConfirmOpen] = useState<boolean>(false);
  const [isPublishing, setIsPublishing] = useState<boolean>(false);

  // Repair State
  const [isRepairModalOpen, setIsRepairModalOpen] = useState<boolean>(false);
  const [repairReason, setRepairReason] = useState<string>("Invigilator illness disruption");
  const [disruptedResources, setDisruptedResources] = useState<string>("");
  const [affectedActivities, setAffectedActivities] = useState<string>("");
  const [isRepairing, setIsRepairing] = useState<boolean>(false);
  const [repairError, setRepairError] = useState<string | null>(null);
  const [repairOutcome, setRepairOutcome] = useState<ScheduleRepairResponse | null>(null);

  const tenantId = user?.tenant_id || "tenant-engineering";

  const fetchSchedule = useCallback(async () => {
    if (!scheduleId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiClient.getSchedule(scheduleId, tenantId);
      setSchedule(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load timetable schedule";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [scheduleId, tenantId]);

  useEffect(() => {
    fetchSchedule();
  }, [fetchSchedule]);

  // Publish Action
  const handlePublish = async () => {
    if (!schedule || !schedule.source_job_id) return;
    setIsPublishing(true);
    try {
      const res = await apiClient.publishScheduleJob(
        schedule.source_job_id,
        { principal: user?.principal_id || "operator" },
        tenantId
      );
      showToast(
        "success",
        `Schedule ${res.schedule_id.substring(0, 8)} published successfully as v${res.version}.`,
        "Schedule Published"
      );
      setIsPublishConfirmOpen(false);
      fetchSchedule();
    } catch (err: unknown) {
      let msg = "Failed to publish schedule";
      if (err instanceof ApiError && err.status === 403) {
        msg = "Unauthorized: Principal or policy does not grant schedule:publish authority.";
      } else if (err instanceof Error) {
        msg = err.message;
      }
      showToast("error", msg, "Publication Error");
    } finally {
      setIsPublishing(false);
    }
  };

  // Repair Action
  const handleRepairSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!scheduleId) return;

    setIsRepairing(true);
    setRepairError(null);

    try {
      const payload: ScheduleRepairRequest = {
        reason: repairReason.trim() || "Operational repair",
        disrupted_resource_ids: disruptedResources
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        affected_activity_ids: affectedActivities
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        movement_weight: 1.0,
      };

      const outcome = await apiClient.repairSchedule(scheduleId, payload, tenantId);
      setRepairOutcome(outcome);
      showToast(
        "success",
        `Repair created candidate schedule version with job ID ${outcome.job_id.substring(0, 8)}.`,
        "Repair Job Dispatched"
      );
    } catch (err: unknown) {
      let msg = "Failed to execute schedule repair";
      if (
        (err instanceof ApiError && (err.status === 400 && (err.message?.includes("not_published") || err.code === "schedule.not_published"))) ||
        (err instanceof Error && err.message?.includes("not_published"))
      ) {
        msg = "Prerequisite Violation: Schedule repair requires a PUBLISHED schedule baseline.";
      } else if (err instanceof Error) {
        msg = err.message;
      }
      setRepairError(msg);
    } finally {
      setIsRepairing(false);
    }
  };

  if (loading) {
    return <LoadingState message="Loading timetable assignment matrix..." />;
  }

  if (error || !schedule) {
    return (
      <ErrorState
        title="Schedule Unavailable"
        message={error || `Schedule ${scheduleId} could not be retrieved.`}
        onRetry={fetchSchedule}
      />
    );
  }

  const assignments: ScheduleAssignmentItem[] = schedule.assignments || [];

  // Group assignments by resource for desktop grid
  const resourceGroups = new Map<string, ScheduleAssignmentItem[]>();
  assignments.forEach((a) => {
    const list = resourceGroups.get(a.resource_id) || [];
    list.push(a);
    resourceGroups.set(a.resource_id, list);
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Link to="/schedules" className="hover:text-slate-200 flex items-center gap-1">
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Schedules
        </Link>
        <span>/</span>
        <span className="font-mono text-slate-300">{schedule.schedule_key}</span>
      </div>

      <PageHeader
        title={`${schedule.schedule_key} (v${schedule.version})`}
        description={`Authoritative timetable assignments • Status: ${schedule.status}`}
        actions={
          <div className="flex items-center gap-3">
            <div className="hidden sm:flex items-center bg-slate-900 border border-slate-800 rounded-lg p-0.5">
              <button
                onClick={() => setViewMode("grid")}
                className={`p-1.5 rounded text-xs flex items-center gap-1 font-medium transition-colors ${
                  viewMode === "grid"
                    ? "bg-slate-800 text-white shadow-sm"
                    : "text-slate-400 hover:text-slate-200"
                }`}
                aria-label="Grid timetable view"
              >
                <LayoutGrid className="w-3.5 h-3.5" /> Grid
              </button>
              <button
                onClick={() => setViewMode("table")}
                className={`p-1.5 rounded text-xs flex items-center gap-1 font-medium transition-colors ${
                  viewMode === "table"
                    ? "bg-slate-800 text-white shadow-sm"
                    : "text-slate-400 hover:text-slate-200"
                }`}
                aria-label="Table timetable view"
              >
                <List className="w-3.5 h-3.5" /> Table
              </button>
            </div>

            <Button
              variant="secondary"
              size="sm"
              onClick={fetchSchedule}
              disabled={loading}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className="w-3.5 h-3.5" /> Refresh
            </Button>

            {/* Repair Action */}
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setRepairOutcome(null);
                setRepairError(null);
                setIsRepairModalOpen(true);
              }}
              disabled={user !== null && !user.roles?.some((r) => ["scheduler", "operator", "tenant_admin", "admin"].includes(r)) && !user.permissions?.some((p) => ["schedule:repair", "*"].includes(p))}
              title={user !== null && !user.roles?.some((r) => ["scheduler", "operator", "tenant_admin", "admin"].includes(r)) && !user.permissions?.some((p) => ["schedule:repair", "*"].includes(p)) ? "Repair requires scheduler, operator, or tenant_admin role" : undefined}
              className="flex items-center gap-1.5 text-amber-300 border-amber-500/30 hover:bg-amber-500/10"
            >
              <Wrench className="w-3.5 h-3.5" /> Repair Schedule
            </Button>

            {/* Publish Action */}
            {schedule.status !== "PUBLISHED" && schedule.source_job_id && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => setIsPublishConfirmOpen(true)}
                disabled={isPublishing || (user !== null && !user.roles?.some((r) => ["scheduler", "operator", "approver", "tenant_admin", "admin"].includes(r)) && !user.permissions?.some((p) => ["schedule:publish", "*"].includes(p)))}
                title={user !== null && !user.roles?.some((r) => ["scheduler", "operator", "approver", "tenant_admin", "admin"].includes(r)) && !user.permissions?.some((p) => ["schedule:publish", "*"].includes(p)) ? "Publish requires scheduler, operator, or approver role" : undefined}
                className="flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white"
              >
                <Send className="w-3.5 h-3.5" />
                {isPublishing ? "Publishing..." : "Publish Timetable"}
              </Button>
            )}
          </div>
        }
      />

      {/* Overview Stat Badges */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-3">
        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-400">Schedule Status</span>
          <div>
            {schedule.status === "PUBLISHED" ? (
              <Badge variant="success" className="flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3" /> PUBLISHED
              </Badge>
            ) : (
              <Badge variant="neutral">{schedule.status}</Badge>
            )}
          </div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-400">Total Assignments</span>
          <div className="text-base font-bold text-white">{assignments.length}</div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-400">Version</span>
          <div className="text-base font-bold text-white">v{schedule.version}</div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-400">Publication Timestamp</span>
          <div className="text-xs font-semibold text-slate-200">
            {schedule.published_at
              ? new Date(schedule.published_at).toLocaleString()
              : "Unpublished"}
          </div>
        </Card>

        <Card className="p-3.5 space-y-1">
          <span className="text-[11px] text-slate-400">Solver Snapshot</span>
          <div className="font-mono text-xs text-slate-300 truncate">
            {schedule.input_snapshot_hash.substring(0, 10)}...
          </div>
        </Card>
      </div>

      {schedule.supersedes_id && (
        <div className="p-3.5 rounded-xl bg-indigo-950/40 border border-indigo-900/60 text-xs text-indigo-300 flex items-center justify-between">
          <span>This schedule is a repaired revision superseding baseline ID: <strong className="font-mono">{schedule.supersedes_id}</strong></span>
          <Link to={`/schedules/${schedule.supersedes_id}`} className="underline hover:text-indigo-200">
            View Predecessor Baseline
          </Link>
        </div>
      )}

      {assignments.length === 0 ? (
        <EmptyState
          title="No Timetable Assignments"
          description="This schedule does not have any assignments associated with it."
        />
      ) : (
        <>
          {/* View 1: Timetable Resource Grid (Desktop & Tablet) */}
          {viewMode === "grid" && (
            <div className="space-y-4">
              <div className="text-xs text-slate-400 flex items-center justify-between">
                <span>Timetable lanes grouped by operational resource.</span>
                <span>{assignments.length} session assignment(s) scheduled.</span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {Array.from(resourceGroups.entries()).map(([resourceId, items]) => (
                  <Card key={resourceId} className="p-4 space-y-3 border-slate-800">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <div className="flex items-center gap-2">
                        <span className="p-1 rounded bg-indigo-500/10 text-indigo-400">
                          <CalendarDays className="w-4 h-4" />
                        </span>
                        <span className="font-semibold text-xs text-white">
                          Resource: <span className="font-mono text-indigo-300">{resourceId}</span>
                        </span>
                      </div>
                      <span className="text-[11px] text-slate-400">
                        {items.length} slot(s)
                      </span>
                    </div>

                    <div className="space-y-2">
                      {items.map((assignment) => (
                        <div
                          key={assignment.id}
                          className="p-3 rounded-lg bg-slate-900/80 border border-slate-800 hover:border-slate-700 transition-colors space-y-1.5"
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-semibold text-xs text-slate-200">
                              {assignment.activity_id}
                            </span>
                            {assignment.is_locked ? (
                              <Badge variant="warning" className="flex items-center gap-1">
                                <Lock className="w-2.5 h-2.5" /> LOCKED
                              </Badge>
                            ) : (
                              <Badge variant="neutral" className="flex items-center gap-1 text-[10px]">
                                <Unlock className="w-2.5 h-2.5 text-slate-400" /> Dynamic
                              </Badge>
                            )}
                          </div>

                          <div className="flex items-center gap-3 text-[11px] text-slate-400 font-mono">
                            <div className="flex items-center gap-1">
                              <Clock className="w-3 h-3 text-emerald-400" />
                              <span>{new Date(assignment.start_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                              <span>—</span>
                              <span>{new Date(assignment.end_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                            </div>
                            <span className="text-slate-400">
                              ({new Date(assignment.start_at).toLocaleDateString()})
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {/* View 2: Assignment Table */}
          {viewMode === "table" && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Activity Session</TableHead>
                  <TableHead>Assigned Resource</TableHead>
                  <TableHead>Start Time</TableHead>
                  <TableHead>End Time</TableHead>
                  <TableHead className="w-24">Constraint</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {assignments.map((assignment) => (
                  <TableRow key={assignment.id}>
                    <TableCell className="font-semibold text-slate-200">
                      {assignment.activity_id}
                    </TableCell>

                    <TableCell className="font-mono text-xs text-indigo-300">
                      {assignment.resource_id}
                    </TableCell>

                    <TableCell className="font-mono text-xs text-slate-300">
                      {new Date(assignment.start_at).toLocaleString()}
                    </TableCell>

                    <TableCell className="font-mono text-xs text-slate-300">
                      {new Date(assignment.end_at).toLocaleString()}
                    </TableCell>

                    <TableCell>
                      {assignment.is_locked ? (
                        <Badge variant="warning">LOCKED</Badge>
                      ) : (
                        <Badge variant="neutral">DYNAMIC</Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </>
      )}

      {/* Repair Modal */}
      <Modal
        isOpen={isRepairModalOpen}
        onClose={() => {
          if (!isRepairing) setIsRepairModalOpen(false);
        }}
        title="Execute Operational Schedule Repair"
      >
        <div className="space-y-4">
          <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-xs text-amber-200">
            <span className="font-bold block mb-1">Immutable Baseline Protection:</span>
            Schedule repairs solve with minimal movement penalties and produce a <strong>new candidate schedule version</strong> (v{schedule.version + 1}) rather than mutating the published baseline in place.
          </div>

          {repairError && (
            <div className="p-3 text-xs rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300">
              {repairError}
            </div>
          )}

          {repairOutcome ? (
            <div className="space-y-3 p-4 rounded-xl bg-slate-900 border border-slate-800 text-xs">
              <div className="flex items-center gap-2 text-emerald-400 font-semibold">
                <CheckCircle2 className="w-4 h-4" />
                Repair Job Initiated Successfully
              </div>
              <div className="space-y-1 text-slate-300 font-mono text-[11px]">
                <div>Job ID: {repairOutcome.job_id}</div>
                <div>Baseline: {repairOutcome.baseline_schedule_id}</div>
                {repairOutcome.candidate_schedule_id && (
                  <div>Candidate ID: {repairOutcome.candidate_schedule_id} (v{repairOutcome.candidate_version})</div>
                )}
                <div>Status: {repairOutcome.status}</div>
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-slate-800">
                {repairOutcome.candidate_schedule_id ? (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => {
                      setIsRepairModalOpen(false);
                      navigate(`/schedules/${repairOutcome.candidate_schedule_id}`);
                    }}
                  >
                    View Repaired Candidate Schedule <ArrowRight className="w-3.5 h-3.5 ml-1" />
                  </Button>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setIsRepairModalOpen(false)}
                  >
                    Close
                  </Button>
                )}
              </div>
            </div>
          ) : (
            <form onSubmit={handleRepairSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1">
                  Reason for Repair <span className="text-rose-400">*</span>
                </label>
                <Input
                  value={repairReason}
                  onChange={(e) => setRepairReason(e.target.value)}
                  placeholder="e.g. Invigilator illness / room maintenance conflict"
                  required
                  maxLength={255}
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1">
                  Disrupted Resource IDs (Comma-separated)
                </label>
                <Input
                  value={disruptedResources}
                  onChange={(e) => setDisruptedResources(e.target.value)}
                  placeholder="e.g. invigilator-lead, hall-alpha"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1">
                  Affected Activity IDs (Comma-separated)
                </label>
                <Input
                  value={affectedActivities}
                  onChange={(e) => setAffectedActivities(e.target.value)}
                  placeholder="e.g. exam-session-1, exam-session-2"
                />
              </div>

              <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={isRepairing}
                  onClick={() => setIsRepairModalOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  size="sm"
                  disabled={isRepairing || !repairReason.trim()}
                  className="bg-amber-600 hover:bg-amber-500 text-white"
                >
                  {isRepairing ? "Dispatching Repair..." : "Run CP-SAT Repair"}
                </Button>
              </div>
            </form>
          )}
        </div>
      </Modal>

      {/* Publish Confirm Dialog */}
      <ConfirmDialog
        isOpen={isPublishConfirmOpen}
        onClose={() => setIsPublishConfirmOpen(false)}
        onConfirm={handlePublish}
        title="Publish Timetable Schedule"
        message={`Are you sure you want to publish timetable schedule v${schedule.version}? Once published, assignments become authoritative for live operations and student portals.`}
        confirmLabel={isPublishing ? "Publishing..." : "Publish Timetable"}
        isLoading={isPublishing}
      />
    </div>
  );
};
