import React, { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, RefreshCw, Calendar, Tag } from "lucide-react";
import { apiClient } from "../api/client";
import type { ResourceAvailabilityItem, ResourceCatalogItem } from "../api/types";
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

export const AvailabilityPage: React.FC = () => {
  const { user } = useAuth();

  const [availabilities, setAvailabilities] = useState<ResourceAvailabilityItem[]>([]);
  const [resources, setResources] = useState<ResourceCatalogItem[]>([]);
  const [selectedResourceId, setSelectedResourceId] = useState<string>("ALL");
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const tenantId = user?.tenant_id || "tenant-engineering";

  const fetchAvailability = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [availData, resData] = await Promise.all([
        apiClient.listAvailability(
          selectedResourceId !== "ALL" ? { resource_id: selectedResourceId } : undefined,
          tenantId
        ),
        apiClient.listResources(undefined, tenantId),
      ]);
      setAvailabilities(availData);
      setResources(resData);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load resource availability";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [selectedResourceId, tenantId]);

  useEffect(() => {
    fetchAvailability();
  }, [fetchAvailability]);

  // Lookup map for resource display names
  const resourceMap = new Map<string, ResourceCatalogItem>();
  resources.forEach((r) => resourceMap.set(r.id, r));

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Link to="/resources" className="hover:text-slate-200 flex items-center gap-1">
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Resource Catalog
        </Link>
      </div>

      <PageHeader
        title="Resource Availability Windows"
        description="Shift operational calendars, scheduled blackout intervals, maintenance windows, and reserved capacity."
        actions={
          <div className="flex items-center gap-3">
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchAvailability}
              disabled={loading}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          </div>
        }
      />

      {/* Filter by resource */}
      <Card className="p-4 flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="w-full md:w-96">
          <label className="block text-xs font-semibold text-slate-300 mb-1">
            Filter by Specific Resource
          </label>
          <Select
            value={selectedResourceId}
            onChange={(e) => setSelectedResourceId(e.target.value)}
            options={[
              { value: "ALL", label: "All Resources" },
              ...resources.map((r) => ({
                value: r.id,
                label: `${r.display_label} (${r.resource_key} - ${r.type})`,
              })),
            ]}
          />
        </div>

        <div className="text-xs text-slate-400">
          Showing {availabilities.length} availability interval(s)
        </div>
      </Card>

      {loading && <LoadingState message="Fetching resource availability windows..." />}

      {error && !loading && (
        <ErrorState
          title="Failed to Load Availability"
          message={error}
          onRetry={fetchAvailability}
        />
      )}

      {!loading && !error && availabilities.length === 0 && (
        <EmptyState
          title="No Availability Windows Found"
          description={
            selectedResourceId !== "ALL"
              ? "No specific availability or blackout windows are recorded for this resource."
              : "No availability intervals are currently registered in the engine."
          }
        />
      )}

      {!loading && !error && availabilities.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Resource</TableHead>
              <TableHead className="w-32">Interval Type</TableHead>
              <TableHead>Start Time</TableHead>
              <TableHead>End Time</TableHead>
              <TableHead>Reason / Notes</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {availabilities.map((item, idx) => {
              const res = resourceMap.get(item.resource_id);
              return (
                <TableRow key={idx}>
                  <TableCell>
                    <div className="font-semibold text-slate-100">
                      {res ? res.display_label : item.resource_id}
                    </div>
                    {res && (
                      <div className="font-mono text-[11px] text-slate-400">
                        {res.resource_key} • {res.type}
                      </div>
                    )}
                  </TableCell>

                  <TableCell>
                    <Badge
                      variant={
                        item.interval_type === "AVAILABLE"
                          ? "success"
                          : item.interval_type === "BLACKOUT" || item.interval_type === "UNAVAILABLE"
                          ? "error"
                          : "warning"
                      }
                    >
                      {item.interval_type}
                    </Badge>
                  </TableCell>

                  <TableCell className="font-mono text-xs text-slate-300">
                    <div className="flex items-center gap-1.5">
                      <Calendar className="w-3.5 h-3.5 text-slate-400" />
                      {new Date(item.start_at).toLocaleString()}
                    </div>
                  </TableCell>

                  <TableCell className="font-mono text-xs text-slate-300">
                    <div className="flex items-center gap-1.5">
                      <Calendar className="w-3.5 h-3.5 text-slate-400" />
                      {new Date(item.end_at).toLocaleString()}
                    </div>
                  </TableCell>

                  <TableCell className="text-xs text-slate-300">
                    {item.reason ? (
                      <div className="flex items-center gap-1">
                        <Tag className="w-3 h-3 text-slate-400" />
                        <span>{item.reason}</span>
                      </div>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
};
