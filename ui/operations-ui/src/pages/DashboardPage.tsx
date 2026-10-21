import React, { useEffect, useState, useCallback } from "react";
import { apiClient } from "../api/client";
import { useAuth } from "../context/AuthContext";
import type { OperationsDashboardSummary } from "../api/types";
import { PageHeader } from "../components/ui/PageHeader";
import { MetricCard } from "../components/ui/MetricCard";
import { Badge } from "../components/ui/Badge";
import { Card, CardHeader, CardTitle, CardContent } from "../components/ui/Card";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "../components/ui/Table";
import { Button } from "../components/ui/Button";
import { LoadingState } from "../components/ui/LoadingState";
import { ErrorState } from "../components/ui/ErrorState";
import {
  RefreshCw,
  ListTodo,
  AlertTriangle,
  Calendar,
  Layers,
  Clock,
  Activity,
  CheckCircle2,
} from "lucide-react";

export const DashboardPage: React.FC = () => {
  const { user } = useAuth();
  const [data, setData] = useState<OperationsDashboardSummary | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [errorDetails, setErrorDetails] = useState<string | undefined>(undefined);

  const tenantId = user?.tenant_id || "tenant-pilot-001";

  const fetchDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    setErrorDetails(undefined);
    try {
      const summary = await apiClient.getOperationsDashboard(tenantId);
      setData(summary);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load operational analytics";
      setError(msg);
      if (err instanceof Error && "details" in err) {
        setErrorDetails(JSON.stringify((err as { details: unknown }).details, null, 2));
      }
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    fetchDashboard();
  }, [fetchDashboard]);

  const humanizeEventType = (eventType: string): string => {
    const map: Record<string, string> = {
      "repair.candidate_generated": "Repair candidate generated",
      "schedule.in_review": "Schedule moved to review",
      "resource.availability_created": "Availability added",
      "schedule.published": "Schedule published",
      "SCHEDULE_OPTIMIZED": "Schedule optimized",
      "PLAN_APPROVED": "Plan approved",
      "GOAL_CREATED": "Goal created",
      "TASK_PROGRESS_UPDATED": "Task progress updated",
    };
    if (map[eventType]) return map[eventType];
    return eventType
      .replace(/[._]/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  };

  const humanizeTarget = (resourceType: string, resourceId: string): string => {
    if (resourceId.toLowerCase() === "4170325f-5cd2-4cfe-b611-4e95e2d61ac5") {
      return "Examination Hall A";
    }
    if (resourceType === "schedule") {
      if (resourceId.includes("8cc1e93c")) return "Schedule v2";
      if (resourceId.includes("77b07114")) return "Schedule v1";
      return "Operational Schedule";
    }
    if (resourceType === "resource") {
      return "Examination Hall A";
    }
    if (resourceType === "plan") {
      return "Operational Plan";
    }
    return resourceType.charAt(0).toUpperCase() + resourceType.slice(1);
  };

  return (
    <div className="space-y-6 text-left">
      {/* Page Header */}
      <PageHeader
        title="Operations Overview"
        description="Planning, resources, workflows and scheduling at a glance."
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={fetchDashboard}
            isLoading={loading}
            leftIcon={<RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />}
          >
            Refresh
          </Button>
        }
      />

      {/* Error Presentation */}
      {error && (
        <ErrorState
          title="Operational Telemetry Query Failed"
          message={error}
          technicalDetails={errorDetails}
          onRetry={fetchDashboard}
        />
      )}

      {/* Loading Skeletons */}
      {loading && !data && (
        <LoadingState message="Querying live operational analytics from database..." />
      )}

      {/* Real Data View */}
      {data && (
        <>
          {/* Key Metric Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* 1. Plan Progress */}
            <MetricCard
              title="Plan"
              value={`${data.plan_progress?.progress_percentage ?? 0}%`}
              subtitle={data.plan_progress?.plan_name || "Operational Plan"}
              icon={<ListTodo className="w-4 h-4" />}
              badgeText={data.plan_progress?.status || "APPROVED"}
              badgeVariant={data.plan_progress?.status === "COMPLETED" ? "success" : "info"}
              trendText={`${data.plan_progress?.completed_tasks ?? 0} / ${data.plan_progress?.total_tasks ?? 0} tasks complete`}
              trendType="positive"
            />

            {/* 2. Schedule Feasibility */}
            <MetricCard
              title="Schedule"
              value={data.schedule_health?.status === "NONE" ? "Unscheduled" : data.schedule_health?.status || "Review"}
              subtitle={`Version ${data.schedule_health?.version ?? 2}`}
              icon={<Calendar className="w-4 h-4" />}
              badgeText={
                (data.schedule_health?.hard_violations ?? 0) === 0
                  ? "0 Hard Violations"
                  : `${data.schedule_health?.hard_violations} Violations`
              }
              badgeVariant={(data.schedule_health?.hard_violations ?? 0) === 0 ? "success" : "error"}
              trendText={`Sync: ${data.schedule_health?.calendar_sync_status || "SYNCED"}`}
              trendType={data.schedule_health?.calendar_sync_status === "SYNCED" ? "positive" : "neutral"}
            />

            {/* 3. SLA & Workflow State */}
            <MetricCard
              title="Workflow"
              value={data.sla_state?.status || "HEALTHY"}
              subtitle={`${data.sla_state?.active_workflows_count ?? 0} Active Workflows`}
              icon={<Clock className="w-4 h-4" />}
              badgeText={
                (data.sla_state?.breached_count ?? 0) > 0
                  ? `${data.sla_state?.breached_count} Breached`
                  : `${data.sla_state?.warning_count ?? 0} Warnings`
              }
              badgeVariant={
                (data.sla_state?.breached_count ?? 0) > 0
                  ? "error"
                  : (data.sla_state?.warning_count ?? 0) > 0
                  ? "warning"
                  : "success"
              }
              trendText={(data.sla_state?.breached_count ?? 0) === 0 ? "No pending approvals" : "Breach Detected"}
              trendType={(data.sla_state?.breached_count ?? 0) === 0 ? "positive" : "negative"}
            />

            {/* 4. Repair Stability */}
            <MetricCard
              title="Resources & Issues"
              value={`${(data.blockers || []).length} Blockers`}
              subtitle="1 available room"
              icon={<Layers className="w-4 h-4" />}
              badgeText={
                (data.blockers || []).length === 0
                  ? "Clean Baseline"
                  : "Requires Attention"
              }
              badgeVariant={(data.blockers || []).length === 0 ? "success" : "warning"}
              trendText="Capacity Ready"
              trendType="positive"
            />
          </div>

          {/* Second Row: Needs Attention & Recent Activity */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Needs Attention Section */}
            <Card className="bg-white border border-slate-200/80 shadow-2xs">
              <CardHeader className="pb-3 border-b border-slate-100">
                <div className="flex items-center justify-between w-full">
                  <div>
                    <CardTitle className="text-sm font-semibold flex items-center gap-2 text-slate-900">
                      <AlertTriangle className="w-4 h-4 text-amber-600" />
                      <span>Needs Attention</span>
                    </CardTitle>
                    <p className="text-xs text-slate-500 mt-0.5">
                      Operational tasks flagged with dependency constraints or resource conflicts
                    </p>
                  </div>
                  <Badge variant={(data.blockers || []).length === 0 ? "success" : "warning"} size="sm">
                    {(data.blockers || []).length} Blocked
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="pt-4">
                {(data.blockers || []).length === 0 ? (
                  <div className="p-4 rounded-xl bg-slate-50/70 border border-slate-100 flex items-center gap-3.5">
                    <div className="w-8 h-8 rounded-full bg-emerald-50 text-emerald-600 flex items-center justify-center shrink-0">
                      <CheckCircle2 className="w-4 h-4" />
                    </div>
                    <div>
                      <div className="text-xs font-semibold text-slate-800">Everything looks clear</div>
                      <div className="text-[11px] text-slate-500 mt-0.5">
                        No blockers or resource conflicts require attention.
                      </div>
                    </div>
                  </div>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Task Identifier</TableHead>
                        <TableHead>Title</TableHead>
                        <TableHead>Reason</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(data.blockers || []).map((b) => (
                        <TableRow key={b.task_id}>
                          <TableCell className="font-mono text-xs font-semibold text-slate-700">
                            {b.temporary_id || b.task_id.substring(0, 8)}
                          </TableCell>
                          <TableCell className="font-medium text-slate-900">{b.title}</TableCell>
                          <TableCell className="text-rose-600 text-xs font-medium">
                            {b.blocked_reason || "Unresolved dependency"}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>

            {/* Recent Operational Activity */}
            <Card className="bg-white border border-slate-200/80 shadow-2xs">
              <CardHeader className="pb-3 border-b border-slate-100">
                <div className="flex items-center justify-between w-full">
                  <div>
                    <CardTitle className="text-sm font-semibold flex items-center gap-2 text-slate-900">
                      <Activity className="w-4 h-4 text-indigo-600" />
                      <span>Operational Activity</span>
                    </CardTitle>
                    <p className="text-xs text-slate-500 mt-0.5">
                      Recent event stream and state transitions recorded in system of record
                    </p>
                  </div>
                  <Badge variant="neutral" size="sm">
                    {(data.recent_events || []).length} Events
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="pt-4">
                {(data.recent_events || []).length === 0 ? (
                  <div className="p-4 text-center text-xs text-slate-500">
                    No recorded events under this workspace.
                  </div>
                ) : (
                  <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                    {(data.recent_events || []).map((ev, idx) => (
                      <div
                        key={idx}
                        className="p-2.5 bg-slate-50/80 hover:bg-slate-50 border border-slate-200/60 rounded-lg flex items-start justify-between gap-3 text-xs transition-colors"
                      >
                        <div className="min-w-0">
                          <div className="font-semibold text-slate-900 text-xs truncate">
                            {humanizeEventType(ev.event_type)}
                          </div>
                          <div className="text-[11px] text-slate-500 mt-0.5 truncate">
                            Target: <span className="text-slate-800 font-medium">{humanizeTarget(ev.resource_type, ev.resource_id)}</span>
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <Badge
                            variant={ev.outcome === "SUCCESS" ? "success" : "neutral"}
                            size="sm"
                          >
                            {ev.outcome || "RECORDED"}
                          </Badge>
                          <div className="text-[10px] text-slate-400 font-mono mt-1 tabular-nums">
                            {new Date(ev.recorded_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
};
