import React, { useState, useEffect, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  CalendarDays,
  ArrowRight,
  RefreshCw,
  CheckCircle2,
} from "lucide-react";
import { apiClient } from "../api/client";
import { formatStatus, formatDateTime } from "../utils/formatters";
import type { ScheduleListItem } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
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
                Run Scheduler
              </Button>
            </Link>
          </div>
        }
      />

      {/* Compact Toolbar */}
      <div className="flex items-center justify-between gap-4 py-1">
        <div className="flex items-center gap-2.5">
          <span className="text-xs font-semibold text-slate-700">Status:</span>
          <div className="w-52">
            <Select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              options={[
                { value: "ALL", label: "All Statuses" },
                { value: "PUBLISHED", label: "Published Only" },
                { value: "DRAFT", label: "Draft / Review" },
              ]}
            />
          </div>
        </div>

        <div className="text-xs text-slate-500 tabular-nums font-medium">
          {schedules.length} {schedules.length === 1 ? "schedule version" : "schedule versions"}
        </div>
      </div>

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
          description="Schedules are produced by the CP-SAT solver. Run the scheduler after an approved plan and resources are ready."
          actionLabel="Run Scheduler"
          onAction={() => navigate("/scheduling/jobs")}
        />
      )}

      {!loading && !error && schedules.length > 0 && (
        <div className="bg-white border border-slate-200/80 rounded-xl overflow-hidden shadow-2xs">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50/70 border-b border-slate-200/80">
                <TableHead className="w-48">Schedule</TableHead>
                <TableHead className="w-24">Version</TableHead>
                <TableHead className="w-28">Status</TableHead>
                <TableHead className="w-36">Origin</TableHead>
                <TableHead>Published</TableHead>
                <TableHead className="w-32 text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {schedules.map((item) => {
                const originLabel =
                  item.version > 1 || item.repair_reason
                    ? `Repair of v${Math.max(1, item.version - 1)}`
                    : "Initial schedule";

                return (
                  <TableRow key={item.id} className="hover:bg-slate-50/50 transition-colors">
                    <TableCell>
                      <div className="font-semibold text-slate-900 text-xs flex items-center gap-2">
                        <CalendarDays className="w-4 h-4 text-indigo-600 shrink-0" />
                        <span>{item.schedule_key}</span>
                      </div>
                      <div className="font-mono text-[11px] text-slate-400 mt-0.5">
                        {item.id.substring(0, 8)}...
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

                    <TableCell className="text-xs text-slate-700 font-medium">
                      {originLabel}
                    </TableCell>

                    <TableCell className="text-xs text-slate-700">
                      {item.published_at ? (
                        <div>
                          <div className="tabular-nums font-medium text-slate-800">
                            {formatDateTime(item.published_at)}
                          </div>
                          {item.published_by && (
                            <div className="text-[11px] text-slate-400">By {item.published_by}</div>
                          )}
                        </div>
                      ) : (
                        <span className="text-slate-400 text-xs">Unpublished</span>
                      )}
                    </TableCell>

                    <TableCell className="text-right">
                      <Link
                        to={`/schedules/${item.id}`}
                        className="inline-flex items-center gap-1 text-xs font-semibold text-indigo-600 hover:text-indigo-800"
                      >
                        View Schedule <ArrowRight className="w-3.5 h-3.5" />
                      </Link>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
};
