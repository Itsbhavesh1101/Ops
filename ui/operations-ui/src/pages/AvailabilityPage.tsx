import React, { useState, useEffect, useCallback } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowLeft, RefreshCw, Calendar, Tag, Plus } from "lucide-react";
import { apiClient } from "../api/client";
import type { ResourceAvailabilityItem, ResourceCatalogItem } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../components/ui/Toast";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Input } from "../components/ui/Input";
import { Select } from "../components/ui/Select";
import { Modal } from "../components/ui/Modal";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "../components/ui/Table";
import { LoadingState } from "../components/ui/LoadingState";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";

import { formatDateTimeRange } from "../utils/formatters";

export const AvailabilityPage: React.FC = () => {
  const { user } = useAuth();
  const { showToast } = useToast();
  const [searchParams] = useSearchParams();

  const [availabilities, setAvailabilities] = useState<ResourceAvailabilityItem[]>([]);
  const [resources, setResources] = useState<ResourceCatalogItem[]>([]);
  const [selectedResourceId, setSelectedResourceId] = useState<string>(
    searchParams.get("resource_id") || "ALL"
  );
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Add Window Modal State
  const [isAddModalOpen, setIsAddModalOpen] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [formResourceId, setFormResourceId] = useState<string>("");
  const [formIntervalType, setFormIntervalType] = useState<"AVAILABLE" | "UNAVAILABLE">("AVAILABLE");
  const [formStartAt, setFormStartAt] = useState<string>("");
  const [formEndAt, setFormEndAt] = useState<string>("");
  const [formReason, setFormReason] = useState<string>("");

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

  const handleCreateAvailability = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formResourceId) {
      setSubmitError("Please select a resource.");
      return;
    }
    if (!formStartAt || !formEndAt) {
      setSubmitError("Start and End date/time are required.");
      return;
    }
    const startIso = new Date(formStartAt).toISOString();
    const endIso = new Date(formEndAt).toISOString();
    if (new Date(endIso) < new Date(startIso)) {
      setSubmitError("End time cannot be earlier than start time.");
      return;
    }

    setIsSubmitting(true);
    setSubmitError(null);
    try {
      await apiClient.createAvailability(
        {
          resource_id: formResourceId,
          interval_type: formIntervalType,
          start_at: startIso,
          end_at: endIso,
          reason: formReason.trim() || undefined,
        },
        tenantId
      );

      showToast("success", "Availability window registered successfully.", "Window Created");
      setIsAddModalOpen(false);
      setFormResourceId("");
      setFormStartAt("");
      setFormEndAt("");
      setFormReason("");
      fetchAvailability();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to record availability window";
      setSubmitError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <Link to="/resources" className="hover:text-slate-800 flex items-center gap-1 transition-colors">
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Resource Catalog
        </Link>
      </div>

      <PageHeader
        title="Resource Availability"
        description="Shift operational calendars, scheduled blackout intervals, maintenance windows, and reserved capacity."
        actions={
          <div className="flex items-center gap-2.5 flex-wrap">
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
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                setSubmitError(null);
                setFormResourceId(
                  selectedResourceId !== "ALL" ? selectedResourceId : resources[0]?.id || ""
                );
                setIsAddModalOpen(true);
              }}
              className="flex items-center gap-1.5"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Add Availability</span>
              <span className="sr-only">Add Window</span>
            </Button>
          </div>
        }
      />

      {/* Compact Inline Resource Filter Toolbar */}
      <div className="flex items-center justify-between gap-4 py-1">
        <div className="flex items-center gap-2.5">
          <span className="text-xs font-semibold text-slate-700">Resource:</span>
          <div className="w-64">
            <Select
              value={selectedResourceId}
              onChange={(e) => setSelectedResourceId(e.target.value)}
              options={[
                { value: "ALL", label: "All Resources" },
                ...resources.map((r) => ({
                  value: r.id,
                  label: `${r.display_label} (${r.resource_key})`,
                })),
              ]}
            />
          </div>
        </div>

        <div className="text-xs text-slate-500 tabular-nums font-medium">
          {availabilities.length} {availabilities.length === 1 ? "interval" : "intervals"}
        </div>
      </div>

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
        <div className="bg-white border border-slate-200/80 rounded-xl overflow-hidden shadow-2xs">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50/70 border-b border-slate-200/80">
                <TableHead>Resource</TableHead>
                <TableHead className="w-36">Interval Type</TableHead>
                <TableHead>Schedule Window</TableHead>
                <TableHead>Reason / Notes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {availabilities.map((item, idx) => {
                const res = resourceMap.get(item.resource_id);
                return (
                  <TableRow key={idx} className="hover:bg-slate-50/50 transition-colors">
                    <TableCell>
                      <div className="font-semibold text-slate-900 text-xs">
                        {res ? res.display_label : item.resource_id}
                      </div>
                      {res && (
                        <div className="font-mono text-[11px] text-slate-500 mt-0.5">
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
                        {item.interval_type === "AVAILABLE" ? "Available" : item.interval_type === "UNAVAILABLE" ? "Unavailable" : item.interval_type}
                      </Badge>
                    </TableCell>

                    <TableCell className="text-xs text-slate-800 font-medium">
                      <div className="flex items-center gap-1.5">
                        <Calendar className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                        <span>{formatDateTimeRange(item.start_at, item.end_at)}</span>
                      </div>
                    </TableCell>

                    <TableCell className="text-xs text-slate-600">
                      {item.reason ? (
                        <div className="flex items-center gap-1.5">
                          <Tag className="w-3 h-3 text-slate-400 shrink-0" />
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
        </div>
      )}

      {/* Add Availability Modal */}
      <Modal
        isOpen={isAddModalOpen}
        onClose={() => !isSubmitting && setIsAddModalOpen(false)}
        title="Add Operational Availability Window"
        description="Define an active operating shift or scheduled blackout / maintenance interval."
        maxWidth="md"
        footer={
          <div className="flex items-center justify-end gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setIsAddModalOpen(false)}
              disabled={isSubmitting}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleCreateAvailability}
              disabled={isSubmitting}
              className="flex items-center gap-1.5"
            >
              {isSubmitting ? (
                <>
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <Plus className="w-3.5 h-3.5" />
                  Save Window
                </>
              )}
            </Button>
          </div>
        }
      >
        <form onSubmit={handleCreateAvailability} className="space-y-4">
          {submitError && (
            <div className="p-3 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs">
              {submitError}
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1">
              Select Target Resource <span className="text-rose-500">*</span>
            </label>
            <Select
              value={formResourceId}
              onChange={(e) => setFormResourceId(e.target.value)}
              disabled={isSubmitting}
              options={[
                { value: "", label: "-- Choose Resource --" },
                ...resources.map((r) => ({
                  value: r.id,
                  label: `${r.display_label} (${r.resource_key} - ${r.type})`,
                })),
              ]}
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1">
              Interval Type <span className="text-rose-500">*</span>
            </label>
            <Select
              value={formIntervalType}
              onChange={(e) => setFormIntervalType(e.target.value as "AVAILABLE" | "UNAVAILABLE")}
              disabled={isSubmitting}
              options={[
                { value: "AVAILABLE", label: "AVAILABLE (Active Operational Shift)" },
                { value: "UNAVAILABLE", label: "UNAVAILABLE (Blackout / Reserved / Maintenance)" },
              ]}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Start Time <span className="text-rose-500">*</span>
              </label>
              <Input
                type="datetime-local"
                value={formStartAt}
                onChange={(e) => setFormStartAt(e.target.value)}
                required
                disabled={isSubmitting}
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                End Time <span className="text-rose-500">*</span>
              </label>
              <Input
                type="datetime-local"
                value={formEndAt}
                onChange={(e) => setFormEndAt(e.target.value)}
                required
                disabled={isSubmitting}
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1">
              Reason / Operational Notes
            </label>
            <Input
              value={formReason}
              onChange={(e) => setFormReason(e.target.value)}
              placeholder="e.g., Examination Shift A or Scheduled Deep Cleaning"
              disabled={isSubmitting}
            />
          </div>
        </form>
      </Modal>
    </div>
  );
};
