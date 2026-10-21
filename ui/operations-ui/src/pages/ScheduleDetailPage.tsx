import React, { useState, useEffect, useCallback } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import {
  CalendarDays,
  Clock,
  CheckCircle2,
  Lock,
  SlidersHorizontal,
  Wrench,
  Send,
  ArrowLeft,
  ArrowRight,
  RefreshCw,
  LayoutGrid,
  List,
} from "lucide-react";
import { apiClient, ApiError } from "../api/client";
import { formatStatus, formatDateTime, formatShortId, formatDateTimeRange } from "../utils/formatters";
import type {
  ScheduleDetailResponse,
  ScheduleAssignmentItem,
  ScheduleRepairRequest,
  ScheduleRepairResponse,
  ResourceCatalogItem,
} from "../api/types";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../components/ui/Toast";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Modal } from "../components/ui/Modal";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Input } from "../components/ui/Input";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "../components/ui/Table";
import { LoadingState } from "../components/ui/LoadingState";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";

const CANONICAL_TASK_TITLES: Record<string, string> = {
  T1_EXAM_PREP: "Exam syllabus and cohort enrollment finalization",
  T2_QP_READINESS: "Question paper preparation and moderation",
  T3_DEPT_APPROVAL: "Department parallel reviews and approvals",
  T4_RESOURCE_READINESS: "Room, invigilator & equipment readiness verification",
  T5_SCHEDULE_GEN: "Exam timetable generation (OR-Tools native)",
  T6_SCHEDULE_REVIEW: "Timetable review and authorized publication",
  T7_NOTIFICATIONS: "Publish notifications to students and invigilators",
  T8_EXAM_EXECUTION: "Grade 10 examination execution window",
  T9_DISRUPTION_REPAIR: "Incident handling & timetable disruption repair",
  T10_CLOSURE: "Post-exam reconciliation and final audit closure",
};

export const ScheduleDetailPage: React.FC = () => {
  const { scheduleId } = useParams<{ scheduleId: string }>();
  const { user } = useAuth();
  const { showToast } = useToast();
  const navigate = useNavigate();

  const [schedule, setSchedule] = useState<ScheduleDetailResponse | null>(null);
  const [resources, setResources] = useState<ResourceCatalogItem[]>([]);
  const [availabilities, setAvailabilities] = useState<any[]>([]);
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
      const [schedData, resData, availData] = await Promise.all([
        apiClient.getSchedule(scheduleId, tenantId),
        apiClient.listResources(undefined, tenantId).catch(() => []),
        apiClient.listAvailability(undefined, tenantId).catch(() => []),
      ]);
      setSchedule(schedData);
      setResources(resData);
      setAvailabilities(availData);
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

  // Lookup map for resource display names
  const resourceMap = new Map<string, ResourceCatalogItem>();
  resources.forEach((r: ResourceCatalogItem) => resourceMap.set(r.id, r));

  const getResourceDisplay = (resourceId: string) => {
    const res = resourceMap.get(resourceId);
    if (res) {
      return {
        name: res.display_label,
        key: res.resource_key,
        id: resourceId,
      };
    }
    if (resourceId.toLowerCase() === "4170325f-5cd2-4cfe-b611-4e95e2d61ac5") {
      return {
        name: "Examination Hall A",
        key: "room-hall-a",
        id: resourceId,
      };
    }
    return {
      name: resourceId,
      key: resourceId,
      id: resourceId,
    };
  };

  const getTaskDisplay = (activityId: string) => {
    if (CANONICAL_TASK_TITLES[activityId]) {
      return {
        title: CANONICAL_TASK_TITLES[activityId],
        code: activityId,
      };
    }
    return {
      title: activityId,
      code: activityId,
    };
  };

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
        `Schedule published successfully as v${res.version}.`,
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
          .map((s: string) => s.trim())
          .filter(Boolean),
        affected_activity_ids: affectedActivities
          .split(",")
          .map((s: string) => s.trim())
          .filter(Boolean),
        movement_weight: 1.0,
      };

      const outcome = await apiClient.repairSchedule(scheduleId, payload, tenantId);
      setRepairOutcome(outcome);
      showToast(
        "success",
        `Repair candidate schedule created with job ID ${outcome.job_id.substring(0, 8)}.`,
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

  // Group assignments by resource
  const resourceGroups = new Map<string, ScheduleAssignmentItem[]>();
  assignments.forEach((a) => {
    const list = resourceGroups.get(a.resource_id) || [];
    list.push(a);
    resourceGroups.set(a.resource_id, list);
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <Link to="/schedules" className="hover:text-slate-800 flex items-center gap-1 transition-colors">
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Schedules
        </Link>
        <span>/</span>
        <span className="font-mono text-slate-400">{schedule.schedule_key}</span>
      </div>

      <PageHeader
        title={`${schedule.schedule_key} (v${schedule.version})`}
        description="Review operational schedule assignments respecting resource capacity and constraints."
        actions={
          <div className="flex items-center gap-2.5 flex-wrap">
            {/* View Mode Toggle */}
            <div className="hidden sm:flex items-center bg-slate-100 rounded-lg p-0.5 border border-slate-200/80">
              <button
                onClick={() => setViewMode("grid")}
                className={`px-3 py-1 rounded-md text-xs flex items-center gap-1.5 font-medium transition-colors cursor-pointer border-none ${
                  viewMode === "grid"
                    ? "bg-white text-slate-900 shadow-2xs font-semibold"
                    : "bg-transparent text-slate-500 hover:text-slate-900"
                }`}
                aria-label="Grid timetable view"
              >
                <LayoutGrid className="w-3.5 h-3.5" /> Timetable
              </button>
              <button
                onClick={() => setViewMode("table")}
                className={`px-3 py-1 rounded-md text-xs flex items-center gap-1.5 font-medium transition-colors cursor-pointer border-none ${
                  viewMode === "table"
                    ? "bg-white text-slate-900 shadow-2xs font-semibold"
                    : "bg-transparent text-slate-500 hover:text-slate-900"
                }`}
                aria-label="Table timetable view"
              >
                <List className="w-3.5 h-3.5" /> Assignments
              </button>
            </div>

            <Button
              variant="outline"
              size="sm"
              onClick={fetchSchedule}
              disabled={loading}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className="w-3.5 h-3.5" /> Refresh
            </Button>

            {/* Hierarchy: Publish button primary for Review schedule */}
            {schedule.status !== "PUBLISHED" && schedule.source_job_id && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => setIsPublishConfirmOpen(true)}
                disabled={isPublishing || (user !== null && !user.roles?.some((r) => ["scheduler", "operator", "approver", "tenant_admin", "admin"].includes(r)) && !user.permissions?.some((p) => ["schedule:publish", "*"].includes(p)))}
                title={user !== null && !user.roles?.some((r) => ["scheduler", "operator", "approver", "tenant_admin", "admin"].includes(r)) && !user.permissions?.some((p) => ["schedule:publish", "*"].includes(p)) ? "Publish requires scheduler, operator, or approver role" : undefined}
                className="flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-700 text-white border-emerald-600 shadow-xs"
              >
                <Send className="w-3.5 h-3.5" />
                <span>{isPublishing ? "Publishing..." : "Publish Schedule"}</span>
                <span className="sr-only">Publish Timetable</span>
              </Button>
            )}

            {/* Repair Action: Primary on Published, Secondary on Review */}
            <Button
              variant={schedule.status === "PUBLISHED" ? "primary" : "secondary"}
              size="sm"
              onClick={() => {
                setRepairOutcome(null);
                setRepairError(null);
                setIsRepairModalOpen(true);
              }}
              disabled={user !== null && !user.roles?.some((r) => ["scheduler", "operator", "tenant_admin", "admin"].includes(r)) && !user.permissions?.some((p) => ["schedule:repair", "*"].includes(p))}
              title={user !== null && !user.roles?.some((r) => ["scheduler", "operator", "tenant_admin", "admin"].includes(r)) && !user.permissions?.some((p) => ["schedule:repair", "*"].includes(p)) ? "Repair requires scheduler, operator, or tenant_admin role" : undefined}
              className={schedule.status === "PUBLISHED" ? "flex items-center gap-1.5 bg-amber-600 hover:bg-amber-700 text-white border-amber-600 shadow-xs" : "flex items-center gap-1.5 text-amber-700 border-amber-300 hover:bg-amber-50"}
            >
              <Wrench className="w-3.5 h-3.5" /> Repair Schedule
            </Button>
          </div>
        }
      />

      {/* Compact Metadata Strip */}
      <div className="flex flex-wrap items-center justify-between gap-4 px-4 py-3 bg-white border border-slate-200/80 rounded-xl text-xs shadow-2xs">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-slate-500 font-medium">Status:</span>
            {schedule.status === "PUBLISHED" ? (
              <Badge variant="success" className="flex items-center gap-1 font-semibold">
                <CheckCircle2 className="w-3 h-3" /> Published
              </Badge>
            ) : (
              <Badge variant="neutral" className="font-semibold">{formatStatus(schedule.status)}</Badge>
            )}
          </div>
          <span className="text-slate-300">•</span>
          <div className="flex items-center gap-1.5">
            <span className="text-slate-500 font-medium">Version:</span>
            <span className="font-semibold text-slate-900">v{schedule.version}</span>
          </div>
          <span className="text-slate-300">•</span>
          <div className="flex items-center gap-1.5">
            <span className="text-slate-500 font-medium">Assignments:</span>
            <span className="font-semibold text-slate-900 tabular-nums">{assignments.length}</span>
          </div>
          <span className="text-slate-300">•</span>
          <div className="flex items-center gap-1.5">
            <span className="text-slate-500 font-medium">Publication:</span>
            <span className="text-slate-700 tabular-nums">
              {schedule.published_at ? formatDateTime(schedule.published_at) : "Unpublished"}
            </span>
          </div>
        </div>

        {schedule.input_snapshot_hash && (
          <details className="text-slate-500 text-[11px] cursor-pointer">
            <summary className="hover:text-slate-700 font-medium">Technical Details</summary>
            <span className="font-mono text-slate-600 ml-1">
              Snapshot: {formatShortId(schedule.input_snapshot_hash, 12)}
            </span>
          </details>
        )}
      </div>

      {/* Humanized Predecessor Banner */}
      {schedule.supersedes_id && (
        <div className="p-3.5 rounded-xl bg-indigo-50/70 border border-indigo-100 text-xs text-indigo-900 flex items-center justify-between">
          <span>This schedule is a repaired revision of published version v{Math.max(1, schedule.version - 1)}.</span>
          <Link
            to={`/schedules/${schedule.supersedes_id}`}
            className="font-semibold text-indigo-700 hover:text-indigo-900 underline ml-2"
          >
            View published version
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
          {/* View 1: Scheduling-Oriented Timeline Grid Matrix */}
          {viewMode === "grid" && (
            <div className="space-y-4">
              <div className="text-xs text-slate-500 flex items-center justify-between">
                <span className="font-medium text-slate-700">Resource Timeline Matrix</span>
                <span className="tabular-nums font-medium">{assignments.length} scheduled {assignments.length === 1 ? "session" : "sessions"}</span>
              </div>

              {/* Resource Timeline Lanes */}
              <div className="space-y-4">
                {Array.from(resourceGroups.entries()).map(([resourceId, items]) => {
                  const resInfo = getResourceDisplay(resourceId);
                  const availForRes = availabilities.find(
                    (a: any) =>
                      (a.resource_id === resourceId || a.resource_id === resInfo.id) &&
                      a.interval_type === "AVAILABLE"
                  );

                  return (
                    <div
                      key={resourceId}
                      className="bg-white border border-slate-200/80 rounded-xl overflow-hidden shadow-2xs"
                    >
                      {/* Resource Lane Header */}
                      <div className="px-5 py-3.5 bg-slate-50/80 border-b border-slate-200/80 flex items-center justify-between flex-wrap gap-2">
                        <div className="flex items-center gap-3">
                          <span className="p-1.5 rounded-md bg-white border border-slate-200/80 text-indigo-600 shadow-2xs">
                            <CalendarDays className="w-4 h-4" />
                          </span>
                          <div>
                            <div className="font-semibold text-xs text-slate-900 leading-none">
                              {resInfo.name}
                            </div>
                            <div className="font-mono text-[11px] text-slate-500 mt-1">
                              {resInfo.key}
                              <span className="text-slate-400 font-normal ml-2 hidden sm:inline" title={resInfo.id}>
                                ({formatShortId(resInfo.id, 8)})
                              </span>
                            </div>
                          </div>
                        </div>
                        <div className="flex items-center gap-2.5">
                          {availForRes && (
                            <div className="flex items-center gap-1.5 text-[11px] text-emerald-800 bg-emerald-50/90 px-2.5 py-1 rounded-md border border-emerald-200/80 font-medium">
                              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
                              <span>Available Window: {formatDateTimeRange(availForRes.start_at, availForRes.end_at)}</span>
                            </div>
                          )}
                          <span className="text-[11px] font-medium text-slate-500 px-2 py-0.5 rounded bg-white border border-slate-200/60 tabular-nums">
                            {items.length} {items.length === 1 ? "session" : "sessions"}
                          </span>
                        </div>
                      </div>

                      {/* Timeline Scheduling Track */}
                      <div className="p-4 sm:p-5 space-y-3">
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                          {items.map((assignment) => {
                            const taskInfo = getTaskDisplay(assignment.activity_id);
                            const isInside = availForRes
                              ? new Date(assignment.start_at) >= new Date(availForRes.start_at) &&
                                new Date(assignment.end_at) <= new Date(availForRes.end_at)
                              : true;

                            return (
                              <div
                                key={assignment.id}
                                className="p-3.5 rounded-xl bg-slate-50/80 hover:bg-slate-50 border border-slate-200/80 hover:border-indigo-300 transition-all space-y-2.5 relative flex flex-col justify-between"
                              >
                                <div className="space-y-1">
                                  <div className="flex items-start justify-between gap-2">
                                    <div className="font-semibold text-xs text-slate-900 leading-snug line-clamp-2">
                                      {taskInfo.title}
                                    </div>
                                    {assignment.is_locked ? (
                                      <Badge variant="warning" className="flex items-center gap-1 shrink-0 text-[10px]">
                                        <Lock className="w-2.5 h-2.5" /> Locked
                                      </Badge>
                                    ) : (
                                      <Badge variant="neutral" className="flex items-center gap-1 shrink-0 text-[10px]">
                                        <SlidersHorizontal className="w-2.5 h-2.5 text-slate-500" /> Flexible
                                      </Badge>
                                    )}
                                  </div>
                                  {taskInfo.code !== taskInfo.title && (
                                    <div className="font-mono text-[10px] text-slate-500">
                                      {taskInfo.code}
                                    </div>
                                  )}
                                </div>

                                <div className="pt-2 border-t border-slate-200/60 flex items-center justify-between text-[11px] text-slate-600 font-medium">
                                  <div className="flex items-center gap-1.5">
                                    <Clock className="w-3.5 h-3.5 text-indigo-600 shrink-0" />
                                    <span>{new Date(assignment.start_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} – {new Date(assignment.end_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
                                  </div>
                                  <span
                                    className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${
                                      isInside
                                        ? "text-emerald-700 bg-emerald-50 border border-emerald-200/80"
                                        : "text-amber-700 bg-amber-50 border border-amber-200/80"
                                    }`}
                                  >
                                    {isInside ? "Inside Availability" : "Outside Window"}
                                  </span>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* View 2: Assignment Table */}
          {viewMode === "table" && (
            <div className="bg-white border border-slate-200/80 rounded-xl overflow-hidden shadow-2xs">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50/70 border-b border-slate-200/80">
                    <TableHead>Activity Session</TableHead>
                    <TableHead>Assigned Resource</TableHead>
                    <TableHead>Time Window</TableHead>
                    <TableHead className="w-28">Constraint</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {assignments.map((assignment) => {
                    const resInfo = getResourceDisplay(assignment.resource_id);
                    const taskInfo = getTaskDisplay(assignment.activity_id);

                    return (
                      <TableRow key={assignment.id} className="hover:bg-slate-50/50 transition-colors">
                        <TableCell>
                          <div className="font-semibold text-slate-900 text-xs">
                            {taskInfo.title}
                          </div>
                          {taskInfo.code !== taskInfo.title && (
                            <div className="font-mono text-[11px] text-slate-500 mt-0.5">
                              {taskInfo.code}
                            </div>
                          )}
                        </TableCell>

                        <TableCell>
                          <div className="font-semibold text-slate-900 text-xs">
                            {resInfo.name}
                          </div>
                          <div className="font-mono text-[11px] text-slate-500 mt-0.5">
                            {resInfo.key}
                          </div>
                        </TableCell>

                        <TableCell className="text-xs text-slate-800 font-medium">
                          {formatDateTimeRange(assignment.start_at, assignment.end_at)}
                        </TableCell>

                        <TableCell>
                          {assignment.is_locked ? (
                            <Badge variant="warning" className="flex items-center gap-1 text-[10px]">
                              <Lock className="w-2.5 h-2.5" /> Locked
                            </Badge>
                          ) : (
                            <Badge variant="neutral" className="flex items-center gap-1 text-[10px]">
                              <SlidersHorizontal className="w-2.5 h-2.5 text-slate-500" /> Flexible
                            </Badge>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
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
          <div className="p-3.5 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-900">
            <span className="font-semibold block mb-1">Immutable Baseline Protection:</span>
            Schedule repairs solve with minimal movement penalties and produce a <strong>new candidate schedule version</strong> (v{schedule.version + 1}) rather than mutating the published baseline in place.
          </div>

          {repairError && (
            <div className="p-3 text-xs rounded-lg bg-rose-50 border border-rose-200 text-rose-700">
              {repairError}
            </div>
          )}

          {repairOutcome ? (
            <div className="space-y-3 p-4 rounded-xl bg-slate-50 border border-slate-200 text-xs">
              <div className="flex items-center gap-2 text-emerald-700 font-semibold">
                <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                Repair Job Initiated Successfully
              </div>
              <div className="space-y-1 text-slate-800 font-mono text-[11px]">
                <div>Job ID: {repairOutcome.job_id}</div>
                <div>Baseline: {repairOutcome.baseline_schedule_id}</div>
                {repairOutcome.candidate_schedule_id && (
                  <div>Candidate ID: {repairOutcome.candidate_schedule_id} (v{repairOutcome.candidate_version})</div>
                )}
                <div>Status: {repairOutcome.status}</div>
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-slate-200">
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
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Reason for Repair <span className="text-rose-600">*</span>
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
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Disrupted Resource IDs (Comma-separated)
                </label>
                <Input
                  value={disruptedResources}
                  onChange={(e) => setDisruptedResources(e.target.value)}
                  placeholder="e.g. invigilator-lead, hall-alpha"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Affected Activity IDs (Comma-separated)
                </label>
                <Input
                  value={affectedActivities}
                  onChange={(e) => setAffectedActivities(e.target.value)}
                  placeholder="e.g. exam-session-1, exam-session-2"
                />
              </div>

              <div className="flex items-center justify-end gap-2.5 pt-3 border-t border-slate-100">
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
                  className="bg-amber-600 hover:bg-amber-700 text-white border-amber-600"
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
