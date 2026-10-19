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
import { EmptyState } from "../components/ui/EmptyState";
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

  return (
    <div className="space-y-6 text-left">
      {/* Page Header */}
      <PageHeader
        title="Operations Engine Executive Dashboard"
        description="Unified operational telemetry across Planning, Automated Workflows, Resource Availability, and Constraint Scheduling."
        badge={
          <Badge variant="primary" size="sm">
            Live Telemetry
          </Badge>
        }
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
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* 1. Plan Progress */}
            <MetricCard
              title="Goal & Plan"
              value={`${data.plan_progress?.progress_percentage ?? 0}%`}
              subtitle={data.plan_progress?.plan_name || "Active Plan"}
              icon={<ListTodo className="w-4 h-4" />}
              badgeText={data.plan_progress?.status || "DRAFT"}
              badgeVariant={data.plan_progress?.status === "COMPLETED" ? "success" : "info"}
              trendText={`${data.plan_progress?.completed_tasks ?? 0} / ${data.plan_progress?.total_tasks ?? 0} Tasks`}
              trendType="positive"
            />

            {/* 2. Schedule Feasibility */}
            <MetricCard
              title="Schedule Status"
              value={data.schedule_health?.status || "NO_SCHEDULE"}
              subtitle={`Version ${data.schedule_health?.version ?? 0}`}
              icon={<Calendar className="w-4 h-4" />}
              badgeText={
                (data.schedule_health?.hard_violations ?? 0) === 0
                  ? "0 Hard Violations"
                  : `${data.schedule_health?.hard_violations} Violations`
              }
              badgeVariant={(data.schedule_health?.hard_violations ?? 0) === 0 ? "success" : "error"}
              trendText={`Sync: ${data.schedule_health?.calendar_sync_status || "N/A"}`}
              trendType={data.schedule_health?.calendar_sync_status === "SYNCED" ? "positive" : "neutral"}
            />

            {/* 3. SLA & Workflow State */}
            <MetricCard
              title="Workflow SLA"
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
              trendText={(data.sla_state?.breached_count ?? 0) === 0 ? "SLA Compliant" : "Breach Detected"}
              trendType={(data.sla_state?.breached_count ?? 0) === 0 ? "positive" : "negative"}
            />

            {/* 4. Repair Stability */}
            <MetricCard
              title="Repair Stability"
              value={data.repair_movement?.moved_assignments_count ?? 0}
              subtitle={data.repair_movement?.repair_reason || "No recent repairs"}
              icon={<Layers className="w-4 h-4" />}
              badgeText={
                (data.repair_movement?.locked_assignments_moved ?? 0) > 0
                  ? `${data.repair_movement?.locked_assignments_moved} Locked Moved`
                  : "Stable Baseline"
              }
              badgeVariant={(data.repair_movement?.locked_assignments_moved ?? 0) > 0 ? "warning" : "success"}
              trendText={
                data.risk_summary?.repair_stability
                  ? `Avg Movement: ${data.risk_summary.repair_stability.average_percentage_moved.toFixed(1)}%`
                  : "Minimal Churn"
              }
              trendType="neutral"
            />
          </div>

          {/* Second Row: Open Blockers & Recent Event Stream */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Blockers Section */}
            <Card>
              <CardHeader>
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 text-amber-400" />
                    <span>Identified Operational Blockers</span>
                  </CardTitle>
                  <p className="text-xs text-slate-400 mt-1">
                    Tasks currently flagged with dependency constraints or resource conflicts
                  </p>
                </div>
                <Badge variant={(data.blockers || []).length === 0 ? "success" : "warning"} size="sm">
                  {(data.blockers || []).length} Blocked
                </Badge>
              </CardHeader>
              <CardContent>
                {(data.blockers || []).length === 0 ? (
                  <EmptyState
                    icon={<CheckCircle2 className="w-5 h-5 text-emerald-400" />}
                    title="No Active Blockers"
                    description="All tasks within the current plan scope are clear of unresolved dependency conflicts."
                  />
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
                          <TableCell className="font-mono text-indigo-300">
                            {b.temporary_id || b.task_id.substring(0, 8)}
                          </TableCell>
                          <TableCell className="font-medium text-white">{b.title}</TableCell>
                          <TableCell className="text-rose-300 text-[11px]">
                            {b.blocked_reason || "Unresolved dependency"}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>

            {/* Audit & Event Stream Section */}
            <Card>
              <CardHeader>
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Activity className="w-4 h-4 text-blue-400" />
                    <span>Recent Operational Event Stream</span>
                  </CardTitle>
                  <p className="text-xs text-slate-400 mt-1">
                    Immutable event outbox and state transitions recorded in system of record
                  </p>
                </div>
                <Badge variant="neutral" size="sm">
                  {(data.recent_events || []).length} Events
                </Badge>
              </CardHeader>
              <CardContent>
                {(data.recent_events || []).length === 0 ? (
                  <EmptyState
                    title="No Recorded Events"
                    description="No domain events have been emitted under the current tenant context."
                  />
                ) : (
                  <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                    {(data.recent_events || []).map((ev, idx) => (
                      <div
                        key={idx}
                        className="p-2.5 bg-slate-950/60 border border-slate-800/80 rounded-lg flex items-start justify-between gap-3 text-xs"
                      >
                        <div>
                          <div className="font-mono text-indigo-300 font-semibold">
                            {ev.event_type}
                          </div>
                          <div className="text-[11px] text-slate-400 mt-0.5">
                            Target: <span className="text-slate-300">{ev.resource_type}</span> (
                            <span className="font-mono">{ev.resource_id.substring(0, 8)}...</span>)
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <Badge
                            variant={ev.outcome === "SUCCESS" ? "success" : "neutral"}
                            size="sm"
                          >
                            {ev.outcome || "RECORDED"}
                          </Badge>
                          <div className="text-[10px] text-slate-500 font-mono mt-1">
                            {new Date(ev.recorded_at).toLocaleTimeString()}
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
