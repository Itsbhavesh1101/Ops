import React, { useState, useEffect, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  CalendarDays,
  ArrowRight,
  RefreshCw,
  Wrench,
  CheckCircle2,
} from "lucide-react";
import { apiClient } from "../api/client";
import { formatStatus, formatDateTime, formatShortId } from "../utils/formatters";
import type { ScheduleListItem } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { Select } from "../components/ui/Select";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "../components/ui/Table";
import { LoadingState } from "../components/ui/LoadingState";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";

export const SchedulesPage: React.FC = () => {
  const { user } = useAuth();
  const navigate = useNavigate();

  const [schedules, setSchedules] = useState<ScheduleListItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("ALL");

  const tenantId = user?.tenant_id || "tenant-engineering";

  const fetchSchedules = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiClient.listSchedules(
        statusFilter !== "ALL" ? { status: statusFilter } : undefined,
        tenantId
      );
      setSchedules(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load timetables";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, tenantId]);

  useEffect(() => {
    fetchSchedules();
  }, [fetchSchedules]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Timetables & Schedules"
        description="Authoritative, mathematically optimized operational schedules, timetable assignments, and published baselines."
        actions={
          <div className="flex items-center gap-3">
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchSchedules}
              disabled={loading}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            <Link to="/scheduling/jobs">
              <Button variant="primary" size="sm" className="flex items-center gap-1.5">
                Run Solver Job
              </Button>
            </Link>
          </div>
        }
      />

      {/* Filter Bar */}
      <Card className="p-4 flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="w-full md:w-64">
          <label className="block text-xs font-semibold text-slate-300 mb-1">
            Filter by Publication Status
          </label>
          <Select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            options={[
              { value: "ALL", label: "All Statuses" },
              { value: "PUBLISHED", label: "PUBLISHED Only" },
              { value: "DRAFT", label: "DRAFT / Candidate" },
            ]}
          />
        </div>

        <div className="text-xs text-slate-400">
          Showing {schedules.length} schedule version(s)
        </div>
      </Card>

      {loading && <LoadingState message="Fetching timetables from engine..." />}

      {error && !loading && (
        <ErrorState
          title="Failed to Load Schedules"
          message={error}
          onRetry={fetchSchedules}
        />
      )}

      {!loading && !error && schedules.length === 0 && (
        <EmptyState
          title="No Schedules Found"
          description="Schedules are produced by the CP-SAT solver. Dispatch a scheduling job to generate a timetable."
          actionLabel="Dispatch Scheduling Job"
          onAction={() => navigate("/scheduling/jobs")}
        />
      )}

      {!loading && !error && schedules.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-48">Schedule Key</TableHead>
              <TableHead className="w-24">Version</TableHead>
              <TableHead className="w-32">Status</TableHead>
              <TableHead>Source Job ID</TableHead>
              <TableHead>Publication Details</TableHead>
              <TableHead className="w-28 text-right">Action</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {schedules.map((item) => (
              <TableRow key={item.id}>
                <TableCell>
                  <div className="font-semibold text-slate-100 flex items-center gap-2">
                    <CalendarDays className="w-4 h-4 text-indigo-400" />
                    <span>{item.schedule_key}</span>
                  </div>
                  <div className="font-mono text-[11px] text-slate-400">
                    ID: {item.id.substring(0, 8)}...
                  </div>
                </TableCell>

                <TableCell>
                  <Badge variant="neutral">v{item.version}</Badge>
                </TableCell>

                <TableCell>
                  {item.status === "PUBLISHED" ? (
                    <Badge variant="success" className="flex items-center gap-1">
                      <CheckCircle2 className="w-3 h-3" /> Published
                    </Badge>
                  ) : (
                    <Badge variant="neutral">{formatStatus(item.status)}</Badge>
                  )}
                </TableCell>

                <TableCell className="font-mono text-xs text-slate-400">
                  {item.source_job_id ? `${formatShortId(item.source_job_id)}` : "—"}
                </TableCell>

                <TableCell className="text-xs text-slate-300">
                  {item.published_at ? (
                    <div>
                      <div>{formatDateTime(item.published_at)}</div>
                      {item.published_by && (
                        <div className="text-[11px] text-slate-400">By: {item.published_by}</div>
                      )}
                    </div>
                  ) : item.repair_reason ? (
                    <div className="flex items-center gap-1 text-amber-300">
                      <Wrench className="w-3 h-3 text-amber-400" />
                      <span>Repaired: {item.repair_reason}</span>
                    </div>
                  ) : (
                    <span className="text-slate-400 text-xs">Unpublished candidate</span>
                  )}
                </TableCell>

                <TableCell className="text-right">
                  <Link
                    to={`/schedules/${item.id}`}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-indigo-400 hover:text-indigo-300"
                  >
                    View Timetable <ArrowRight className="w-3.5 h-3.5" />
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
};
