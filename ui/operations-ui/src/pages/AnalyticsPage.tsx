import React, { useState, useEffect, useCallback } from "react";
import {
  RefreshCw,
  ListTodo,
  AlertTriangle,
  Calendar,
  Clock,
  Layers,
  Activity,
  ShieldAlert,
  Search,
  X,
} from "lucide-react";
import { apiClient } from "../api/client";
import { formatDateTime, formatShortId } from "../utils/formatters";
import type { OperationsDashboardSummary } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Card, CardHeader, CardTitle, CardContent } from "../components/ui/Card";
import { MetricCard } from "../components/ui/MetricCard";
import { Input } from "../components/ui/Input";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "../components/ui/Table";
import { LoadingState } from "../components/ui/LoadingState";
import { ErrorState } from "../components/ui/ErrorState";

export const AnalyticsPage: React.FC = () => {
  const { user } = useAuth();

  const [data, setData] = useState<OperationsDashboardSummary | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [goalIdFilter, setGoalIdFilter] = useState<string>("");
  const [activeGoalFilter, setActiveGoalFilter] = useState<string | undefined>(undefined);

  const tenantId = user?.tenant_id;

  const fetchAnalytics = useCallback(async () => {
    if (!tenantId) {
      setError("Active authenticated tenant context is required for operations analytics.");
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const summary = await apiClient.getOperationsDashboard(tenantId, activeGoalFilter);
      setData(summary);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load operational telemetry";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [tenantId, activeGoalFilter]);

  useEffect(() => {
    fetchAnalytics();
  }, [fetchAnalytics]);

  const handleApplyGoalFilter = (e: React.FormEvent) => {
    e.preventDefault();
    const clean = goalIdFilter.trim();
    setActiveGoalFilter(clean ? clean : undefined);
  };

  const handleClearGoalFilter = () => {
    setGoalIdFilter("");
    setActiveGoalFilter(undefined);
  };

  const humanizeEventType = (eventType: string): string => {
    const map: Record<string, string> = {
      "task.progress_updated": "Task Progress Updated",
      "task.completed": "Task Completed",
      "plan.created": "Plan Created",
      "plan.submitted_for_approval": "Plan Submitted for Approval",
      "plan.approved": "Plan Approved",
      "schedule.published": "Schedule Published",
      "schedule.repaired": "Schedule Repaired",
      "resource.availability_created": "Availability Added",
      "workflow.instance_started": "Workflow Instance Started",
      "workflow.task_completed": "Human Task Completed",
    };
    if (map[eventType]) return map[eventType];
    return eventType
      .replace(/[._]/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  };

  if (loading && !data) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Operations Analytics"
          description="Aggregated operational dashboard telemetry and system health rollups"
          breadcrumbs={[
            { label: "Dashboard", href: "/dashboard" },
            { label: "Operations Analytics" },
          ]}
        />
        <LoadingState message="Aggregating operational telemetry from system of record..." />
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Operations Analytics"
          breadcrumbs={[
            { label: "Dashboard", href: "/dashboard" },
            { label: "Operations Analytics" },
          ]}
        />
        <ErrorState
          title="Failed to Load Telemetry"
          message={error}
          onRetry={fetchAnalytics}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6 text-left">
      {/* Header */}
      <PageHeader
        title="Operations Analytics"
        description={`Real-time aggregated operational telemetry for tenant ${tenantId || "unassigned"}`}
        breadcrumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "Operations Analytics" },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchAnalytics}
              disabled={loading}
              className="gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
            </Button>
          </div>
        }
      />

      {/* Goal Filter Toolbar */}
      <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm flex flex-wrap items-center justify-between gap-4">
        <form onSubmit={handleApplyGoalFilter} className="flex items-center gap-2 max-w-md w-full">
          <Input
            placeholder="Filter telemetry by Parent Goal UUID..."
            value={goalIdFilter}
            onChange={(e) => setGoalIdFilter(e.target.value)}
            className="text-xs font-mono"
          />
          <Button type="submit" variant="secondary" size="sm" className="gap-1">
            <Search className="w-3.5 h-3.5" /> Filter
          </Button>
          {activeGoalFilter && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleClearGoalFilter}
              className="text-slate-500 hover:text-slate-700"
            >
              <X className="w-3.5 h-3.5" />
            </Button>
          )}
        </form>
        {activeGoalFilter && (
          <span className="text-xs text-indigo-700 font-mono bg-indigo-50 border border-indigo-200 px-2.5 py-1 rounded-md">
            Filtered by Goal: {formatShortId(activeGoalFilter)}
          </span>
        )}
      </div>

      {data && (
        <>
          {/* Top Metric Cards: 4 Primary Pillars */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <MetricCard
              title="Plan Progress"
              value={data.plan_progress ? `${data.plan_progress.progress_percentage}%` : "No Active Plan"}
              subtitle={
                data.plan_progress
                  ? `${data.plan_progress.completed_tasks} / ${data.plan_progress.total_tasks} tasks`
                  : "No active plan in progress"
              }
              badgeText={
                !data.plan_progress
                  ? "None"
                  : data.plan_progress.progress_percentage === 100
                  ? "Completed"
                  : data.plan_progress.progress_percentage > 0
                  ? "In Progress"
                  : "Pending"
              }
              badgeVariant={
                !data.plan_progress
                  ? "neutral"
                  : data.plan_progress.progress_percentage === 100
                  ? "success"
                  : data.plan_progress.progress_percentage > 0
                  ? "info"
                  : "neutral"
              }
              icon={<ListTodo className="w-5 h-5 text-indigo-600" />}
            />

            <MetricCard
              title="Operational Blockers"
              value={data.blockers ? data.blockers.length : 0}
              subtitle={!data.blockers || data.blockers.length === 0 ? "Zero blockers active" : "Tasks impeded"}
              badgeText={!data.blockers || data.blockers.length === 0 ? "Healthy" : "Blocked"}
              badgeVariant={!data.blockers || data.blockers.length === 0 ? "success" : "error"}
              icon={<AlertTriangle className="w-5 h-5 text-rose-600" />}
            />

            <MetricCard
              title="Workflow SLA State"
              value={data.sla_state?.status || "NO DATA"}
              subtitle={
                data.sla_state
                  ? `${data.sla_state.active_workflows_count} active (${data.sla_state.breached_count} breached)`
                  : "No SLA telemetry available"
              }
              badgeText={data.sla_state?.status || "None"}
              badgeVariant={
                !data.sla_state
                  ? "neutral"
                  : data.sla_state.status === "HEALTHY"
                  ? "success"
                  : data.sla_state.status === "WARNING"
                  ? "warning"
                  : data.sla_state.status === "BREACHED"
                  ? "error"
                  : "neutral"
              }
              icon={<Clock className="w-5 h-5 text-emerald-600" />}
            />

            <MetricCard
              title="Schedule Health"
              value={data.schedule_health?.status || "NO DATA"}
              subtitle={
                data.schedule_health
                  ? `v${data.schedule_health.version} • ${data.schedule_health.hard_violations} violations`
                  : "No schedule telemetry available"
              }
              badgeText={
                !data.schedule_health
                  ? "None"
                  : data.schedule_health.hard_violations === 0
                  ? "Zero Violations"
                  : "Violations"
              }
              badgeVariant={
                !data.schedule_health
                  ? "neutral"
                  : data.schedule_health.hard_violations === 0 && data.schedule_health.status === "PUBLISHED"
                  ? "success"
                  : data.schedule_health.hard_violations > 0
                  ? "error"
                  : "info"
              }
              icon={<Calendar className="w-5 h-5 text-blue-600" />}
            />
          </div>

          {/* Section 1: Plan Progress & Milestones */}
          <Card className="border-slate-200 shadow-sm bg-white">
            <CardHeader className="border-b border-slate-100 flex flex-row items-center justify-between pb-3">
              <CardTitle className="text-base font-semibold text-slate-900 flex items-center gap-2">
                <ListTodo className="w-4 h-4 text-indigo-600" /> Active Plan Delivery Status
              </CardTitle>
              {data.plan_progress?.status && (
                <Badge variant="info">{data.plan_progress.status}</Badge>
              )}
            </CardHeader>
            <CardContent className="p-4 space-y-4">
              {data.plan_progress && data.plan_progress.plan_id ? (
                <>
                  <div className="flex flex-wrap items-center justify-between text-sm">
                    <div>
                      <span className="font-semibold text-slate-900">
                        {data.plan_progress.plan_name || "Operational Plan"}
                      </span>
                      <span className="text-slate-400 font-mono text-xs ml-2">
                        ({formatShortId(data.plan_progress.plan_id)})
                      </span>
                    </div>
                    {data.plan_progress.current_milestone && (
                      <span className="text-xs font-medium text-slate-700 bg-slate-100 px-2.5 py-1 rounded">
                        Target Milestone: <strong>{data.plan_progress.current_milestone}</strong>
                      </span>
                    )}
                  </div>

                  <div className="space-y-1">
                    <div className="flex justify-between text-xs text-slate-600 font-medium">
                      <span>Execution Completion</span>
                      <span>{data.plan_progress.progress_percentage}%</span>
                    </div>
                    <div className="w-full h-2.5 bg-slate-100 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-indigo-600 transition-all duration-300 rounded-full"
                        style={{ width: `${data.plan_progress.progress_percentage}%` }}
                      />
                    </div>
                  </div>
                </>
              ) : (
                <div className="text-center py-6 text-slate-500 text-sm">
                  No Active Plan in Progress
                </div>
              )}
            </CardContent>
          </Card>

          {/* Section 2: Operational Blockers Table */}
          <Card className="border-slate-200 shadow-sm bg-white">
            <CardHeader className="border-b border-slate-100 pb-3 flex flex-row items-center justify-between">
              <CardTitle className="text-base font-semibold text-slate-900 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-rose-600" /> Operational Blockers &amp; Impediments
              </CardTitle>
              <Badge variant={data.blockers.length === 0 ? "success" : "error"}>
                {data.blockers.length} Blocked
              </Badge>
            </CardHeader>
            <CardContent className="p-0">
              {data.blockers.length === 0 ? (
                <div className="p-8 text-center text-slate-500 text-sm">
                  Zero active operational blockers recorded. Work packages are progressing smoothly.
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Task ID</TableHead>
                      <TableHead>Task Title</TableHead>
                      <TableHead>Blocker Reason</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.blockers.map((b) => (
                      <TableRow key={b.task_id}>
                        <TableCell className="font-mono text-xs font-semibold text-indigo-700">
                          {b.temporary_id || formatShortId(b.task_id)}
                        </TableCell>
                        <TableCell className="font-medium text-slate-900">
                          {b.title}
                        </TableCell>
                        <TableCell className="text-xs text-rose-700 bg-rose-50/50">
                          {b.blocked_reason || "Declared blocker (no reason supplied)"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>          {/* Section 3: Risk Summary & Disruption Repair Stability */}
          {data.risk_summary ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Risk Monitor Card */}
              <Card className="border-slate-200 shadow-sm bg-white">
                <CardHeader className="border-b border-slate-100 pb-3">
                  <CardTitle className="text-base font-semibold text-slate-900 flex items-center gap-2">
                    <ShieldAlert className="w-4 h-4 text-amber-600" /> Operational Risk Monitor
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-4 space-y-4">
                  <div className="grid grid-cols-2 gap-3 text-center">
                    <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                      <div className="text-xl font-bold text-slate-900">
                        {data.risk_summary.open_risks_count}
                      </div>
                      <div className="text-[11px] text-slate-500 uppercase tracking-wider font-semibold mt-0.5">
                        Open Risks
                      </div>
                    </div>
                    <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                      <div className="text-xl font-bold text-slate-900">
                        {data.risk_summary.stale_resources_count}
                      </div>
                      <div className="text-[11px] text-slate-500 uppercase tracking-wider font-semibold mt-0.5">
                        Stale Resources
                      </div>
                    </div>
                  </div>

                  {/* Severities */}
                  <div className="space-y-1.5">
                    <span className="text-xs font-semibold text-slate-700">Risk Severity Rollup</span>
                    <div className="flex items-center gap-2 text-xs">
                      <span className="px-2 py-0.5 rounded bg-rose-100 text-rose-800 font-semibold">
                        Critical: {data.risk_summary.risks_by_severity?.CRITICAL || 0}
                      </span>
                      <span className="px-2 py-0.5 rounded bg-amber-100 text-amber-800 font-semibold">
                        High: {data.risk_summary.risks_by_severity?.HIGH || 0}
                      </span>
                      <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-800 font-semibold">
                        Medium: {data.risk_summary.risks_by_severity?.MEDIUM || 0}
                      </span>
                      <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-700 font-semibold">
                        Low: {data.risk_summary.risks_by_severity?.LOW || 0}
                      </span>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Repair Stability Card */}
              <Card className="border-slate-200 shadow-sm bg-white">
                <CardHeader className="border-b border-slate-100 pb-3">
                  <CardTitle className="text-base font-semibold text-slate-900 flex items-center gap-2">
                    <Layers className="w-4 h-4 text-blue-600" /> Disruption Repair Stability
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-4 space-y-4">
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                      <div className="text-lg font-bold text-slate-900">
                        {data.risk_summary.repair_stability?.total_repairs_count || 0}
                      </div>
                      <div className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold mt-0.5">
                        Total Repairs
                      </div>
                    </div>
                    <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                      <div className="text-lg font-bold text-slate-900">
                        {(data.risk_summary.repair_stability?.average_percentage_moved || 0).toFixed(1)}%
                      </div>
                      <div className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold mt-0.5">
                        Avg Churn
                      </div>
                    </div>
                    <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                      <div className="text-lg font-bold text-emerald-600">
                        {data.risk_summary.repair_stability?.locked_moved_count || 0}
                      </div>
                      <div className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold mt-0.5">
                        Locked Churn (0)
                      </div>
                    </div>
                  </div>

                  {data.repair_movement ? (
                    <div className="text-xs text-slate-600 bg-slate-50 p-2.5 rounded border border-slate-100 space-y-1">
                      <div>
                        <span className="font-semibold text-slate-800">Latest Repair Movement: </span>
                        {data.repair_movement.moved_assignments_count} assignments moved
                      </div>
                      {data.repair_movement.repair_reason && (
                        <div className="italic text-slate-500">
                          Reason: {data.repair_movement.repair_reason}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="text-xs text-slate-500 bg-slate-50 p-2.5 rounded border border-slate-100">
                      No repair movements recorded for this window.
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          ) : (
            <div className="p-4 bg-slate-50 border border-slate-200 rounded-xl text-center text-xs text-slate-500">
              Operational risk and repair stability telemetry not currently active for this partition scope.
            </div>
          )}

          {/* Section 4: Recent Chronological Events */}
          <Card className="border-slate-200 shadow-sm bg-white">
            <CardHeader className="border-b border-slate-100 pb-3 flex flex-row items-center justify-between">
              <CardTitle className="text-base font-semibold text-slate-900 flex items-center gap-2">
                <Activity className="w-4 h-4 text-indigo-600" /> Recent Chronological Operational Events
              </CardTitle>
              <span className="text-xs text-slate-500">
                Authoritative event stream from PostgreSQL audit log
              </span>
            </CardHeader>
            <CardContent className="p-0">
              {data.recent_events.length === 0 ? (
                <div className="p-8 text-center text-slate-500 text-sm">
                  No recent operational events recorded for this tenant partition.
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Event</TableHead>
                      <TableHead>Resource</TableHead>
                      <TableHead>Outcome</TableHead>
                      <TableHead>Timestamp</TableHead>
                      <TableHead>Correlation ID</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.recent_events.map((ev, idx) => (
                      <TableRow key={`${ev.event_type}-${idx}`}>
                        <TableCell className="font-medium text-slate-900">
                          {humanizeEventType(ev.event_type)}
                          <div className="text-[10px] font-mono text-slate-400">
                            {ev.event_type}
                          </div>
                        </TableCell>
                        <TableCell className="text-xs text-slate-700">
                          <span className="font-semibold">{ev.resource_type || "generic"}</span>
                          {ev.resource_id && (
                            <span className="text-slate-500 font-mono text-[11px] ml-1">
                              ({formatShortId(ev.resource_id)})
                            </span>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              ev.outcome === "SUCCESS"
                                ? "success"
                                : ev.outcome === "FAILURE"
                                ? "error"
                                : "neutral"
                            }
                            size="sm"
                          >
                            {ev.outcome || "RECORDED"}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs text-slate-600 whitespace-nowrap">
                          {formatDateTime(ev.recorded_at)}
                        </TableCell>
                        <TableCell className="text-xs font-mono text-slate-400">
                          {ev.correlation_id ? formatShortId(ev.correlation_id) : "-"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
};
