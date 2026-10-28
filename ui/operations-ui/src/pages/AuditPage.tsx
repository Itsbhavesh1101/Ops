import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  ShieldCheck,
  Search,
  RefreshCw,
  X,
  ChevronLeft,
  ChevronRight,
  Eye,
  Lock,
  User,
} from "lucide-react";
import { apiClient } from "../api/client";
import { formatDateTime, formatShortId } from "../utils/formatters";
import { redactSensitiveForDisplay } from "../utils/redact";
import type { AuditEventResponse, AuditQueryParams } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Card, CardHeader, CardTitle, CardContent } from "../components/ui/Card";
import { Input } from "../components/ui/Input";
import { Modal } from "../components/ui/Modal";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "../components/ui/Table";
import { LoadingState } from "../components/ui/LoadingState";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";

interface AuditFilters {
  eventType: string;
  resourceType: string;
  resourceId: string;
  correlationId: string;
}

const EMPTY_FILTERS: AuditFilters = {
  eventType: "",
  resourceType: "",
  resourceId: "",
  correlationId: "",
};

export const AuditPage: React.FC = () => {
  const { user } = useAuth();

  const [events, setEvents] = useState<AuditEventResponse[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Draft Filter States (bound to form inputs - typing does not trigger API requests)
  const [draftFilters, setDraftFilters] = useState<AuditFilters>(EMPTY_FILTERS);

  // Explicit Applied Filter States (applied on submission or clear - drives queries)
  const [appliedFilters, setAppliedFilters] = useState<AuditFilters>(EMPTY_FILTERS);

  // Pagination States
  const [limit, setLimit] = useState<number>(25);
  const [offset, setOffset] = useState<number>(0);

  // Audit Details Inspection Modal
  const [selectedEvent, setSelectedEvent] = useState<AuditEventResponse | null>(null);
  const [isDetailModalOpen, setIsDetailModalOpen] = useState<boolean>(false);

  const tenantId = user?.tenant_id;

  const canReadAudit =
    Boolean(user) &&
    Boolean(tenantId) &&
    Boolean(
      user?.roles?.some((r) => ["auditor", "tenant_admin", "admin"].includes(r)) ||
        user?.permissions?.some((p) => ["audit:read", "*"].includes(p))
    );

  // Active context tracking refs for deterministic stale-response and race-condition rejection
  const isMountedRef = useRef<boolean>(true);
  const currentRequestIdRef = useRef<number>(0);
  const activePrincipalIdRef = useRef<string | undefined>(user?.principal_id);
  const activeTenantIdRef = useRef<string | undefined>(tenantId);
  const activeCanReadAuditRef = useRef<boolean>(canReadAudit);
  const prevAuthKeyRef = useRef<string | null>(null);
  const prevFiltersRef = useRef<{ filtersKey: string; offset: number; limit: number }>({
    filtersKey: JSON.stringify(EMPTY_FILTERS),
    offset: 0,
    limit: 25,
  });

  // Mount/unmount lifecycle tracking
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      currentRequestIdRef.current += 1;
    };
  }, []);

  const fetchAuditEvents = useCallback(
    async (
      filtersToUse: AuditFilters = appliedFilters,
      targetOffset: number = offset,
      targetLimit: number = limit
    ) => {
      const currentPrincipalId = user?.principal_id;
      const currentTenantId = tenantId;

      if (!user) {
        setEvents([]);
        setSelectedEvent(null);
        setIsDetailModalOpen(false);
        setError("Authentication session required. Please sign in to inspect audit logs.");
        setLoading(false);
        return;
      }

      if (!tenantId) {
        setEvents([]);
        setSelectedEvent(null);
        setIsDetailModalOpen(false);
        setError("Active authenticated tenant context is required to inspect audit logs.");
        setLoading(false);
        return;
      }

      if (!canReadAudit) {
        setEvents([]);
        setSelectedEvent(null);
        setIsDetailModalOpen(false);
        setError(
          "Insufficient permissions to inspect audit trail. The 'audit:read' permission or 'auditor' role is required."
        );
        setLoading(false);
        return;
      }

      const requestId = ++currentRequestIdRef.current;
      const initiatingPrincipalId = currentPrincipalId;
      const initiatingTenantId = currentTenantId;

      setLoading(true);
      setError(null);

      const params: AuditQueryParams = {
        limit: targetLimit,
        offset: targetOffset,
        event_type: filtersToUse.eventType.trim() || undefined,
        resource_type: filtersToUse.resourceType.trim() || undefined,
        resource_id: filtersToUse.resourceId.trim() || undefined,
        correlation_id: filtersToUse.correlationId.trim() || undefined,
      };

      try {
        const records = await apiClient.searchAudit(params, initiatingTenantId);

        // Deterministic guard against stale responses:
        // Must match current request ID AND current auth context
        if (
          !isMountedRef.current ||
          requestId !== currentRequestIdRef.current ||
          activePrincipalIdRef.current !== initiatingPrincipalId ||
          activeTenantIdRef.current !== initiatingTenantId ||
          !activeCanReadAuditRef.current
        ) {
          return; // Discard stale response without mutating visible state
        }

        setEvents(records);
        setLoading(false);
      } catch (err: unknown) {
        // Deterministic guard against stale errors:
        if (
          !isMountedRef.current ||
          requestId !== currentRequestIdRef.current ||
          activePrincipalIdRef.current !== initiatingPrincipalId ||
          activeTenantIdRef.current !== initiatingTenantId ||
          !activeCanReadAuditRef.current
        ) {
          return; // Discard stale error without mutating visible error state
        }

        const msg = err instanceof Error ? err.message : "Failed to load audit records";
        setError(msg);
        setLoading(false);
      }
    },
    [user, tenantId, canReadAudit, appliedFilters, offset, limit]
  );

  // Synchronized effect for authentication transitions, filter updates, and pagination
  useEffect(() => {
    const currentAuthKey = `${user?.principal_id || ""}:${tenantId || ""}:${Boolean(canReadAudit)}`;
    const authChanged = prevAuthKeyRef.current !== currentAuthKey;

    activePrincipalIdRef.current = user?.principal_id;
    activeTenantIdRef.current = tenantId;
    activeCanReadAuditRef.current = canReadAudit;

    if (authChanged) {
      prevAuthKeyRef.current = currentAuthKey;
      // Invalidate any in-flight requests from prior authentication/tenant context
      currentRequestIdRef.current += 1;

      // TASK 2: Clear sensitive modal state and previously fetched audit records
      setSelectedEvent(null);
      setIsDetailModalOpen(false);
      setEvents([]);
      setDraftFilters(EMPTY_FILTERS);
      setAppliedFilters(EMPTY_FILTERS);
      setOffset(0);

      prevFiltersRef.current = {
        filtersKey: JSON.stringify(EMPTY_FILTERS),
        offset: 0,
        limit,
      };

      if (!user) {
        setError("Authentication session required. Please sign in to inspect audit logs.");
        setLoading(false);
        return;
      }

      if (!tenantId) {
        setError("Active authenticated tenant context is required to inspect audit logs.");
        setLoading(false);
        return;
      }

      if (!canReadAudit) {
        setError(
          "Insufficient permissions to inspect audit trail. The 'audit:read' permission or 'auditor' role is required."
        );
        setLoading(false);
        return;
      }

      setError(null);
      fetchAuditEvents(EMPTY_FILTERS, 0, limit);
      return;
    }

    // Auth did not change; evaluate filter or pagination updates for current authorized session
    if (!user || !tenantId || !canReadAudit) return;

    const currentFiltersKey = JSON.stringify(appliedFilters);
    const filtersChanged =
      prevFiltersRef.current.filtersKey !== currentFiltersKey ||
      prevFiltersRef.current.offset !== offset ||
      prevFiltersRef.current.limit !== limit;

    if (filtersChanged) {
      prevFiltersRef.current = {
        filtersKey: currentFiltersKey,
        offset,
        limit,
      };
      fetchAuditEvents(appliedFilters, offset, limit);
    }
  }, [user, tenantId, canReadAudit, appliedFilters, offset, limit, fetchAuditEvents]);

  const handleFilterSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const isSame =
      draftFilters.eventType === appliedFilters.eventType &&
      draftFilters.resourceType === appliedFilters.resourceType &&
      draftFilters.resourceId === appliedFilters.resourceId &&
      draftFilters.correlationId === appliedFilters.correlationId &&
      offset === 0;

    if (isSame) {
      fetchAuditEvents(draftFilters, 0, limit);
    } else {
      setOffset(0); // Applying new filters resets pagination to the first page
      setAppliedFilters({ ...draftFilters });
    }
  };

  const handleClearFilters = () => {
    const wasAlreadyEmpty =
      appliedFilters.eventType === "" &&
      appliedFilters.resourceType === "" &&
      appliedFilters.resourceId === "" &&
      appliedFilters.correlationId === "" &&
      offset === 0;

    setDraftFilters(EMPTY_FILTERS);
    setOffset(0);
    setAppliedFilters(EMPTY_FILTERS);

    if (wasAlreadyEmpty) {
      fetchAuditEvents(EMPTY_FILTERS, 0, limit);
    }
  };

  const handleRefresh = () => {
    fetchAuditEvents(appliedFilters, offset, limit);
  };

  const openEventDetails = (ev: AuditEventResponse) => {
    setSelectedEvent(ev);
    setIsDetailModalOpen(true);
  };

  const getOutcomeBadge = (outcome?: string | null) => {
    switch (outcome?.toUpperCase()) {
      case "SUCCESS":
        return <Badge variant="success" size="sm">SUCCESS</Badge>;
      case "FAILURE":
        return <Badge variant="error" size="sm">FAILURE</Badge>;
      case "DENIED":
        return <Badge variant="warning" size="sm">DENIED</Badge>;
      default:
        return <Badge variant="neutral" size="sm">{outcome || "RECORDED"}</Badge>;
    }
  };

  const currentPage = Math.floor(offset / limit) + 1;
  const hasNextPage = events.length === limit;
  const hasPrevPage = offset > 0;

  const hasFilterValues = Boolean(
    draftFilters.eventType ||
      draftFilters.resourceType ||
      draftFilters.resourceId ||
      draftFilters.correlationId ||
      appliedFilters.eventType ||
      appliedFilters.resourceType ||
      appliedFilters.resourceId ||
      appliedFilters.correlationId
  );

  return (
    <div className="space-y-6 text-left">
      {/* Header */}
      <PageHeader
        title="Enterprise Audit Explorer"
        description={`Immutable chronological security and state-change ledger for tenant ${tenantId || "unassigned"}`}
        breadcrumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "Audit Explorer" },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={handleRefresh}
              disabled={loading}
              className="gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
            </Button>
          </div>
        }
      />

      {/* Filter Toolbar */}
      <Card className="border-slate-200 shadow-sm bg-white p-4">
        <form onSubmit={handleFilterSubmit} className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Event Type
              </label>
              <Input
                placeholder="e.g. task.progress_updated"
                value={draftFilters.eventType}
                onChange={(e) =>
                  setDraftFilters((prev) => ({ ...prev, eventType: e.target.value }))
                }
                className="text-xs"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Resource Type
              </label>
              <Input
                placeholder="e.g. task, plan, schedule"
                value={draftFilters.resourceType}
                onChange={(e) =>
                  setDraftFilters((prev) => ({ ...prev, resourceType: e.target.value }))
                }
                className="text-xs"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Resource Identifier
              </label>
              <Input
                placeholder="UUID or key"
                value={draftFilters.resourceId}
                onChange={(e) =>
                  setDraftFilters((prev) => ({ ...prev, resourceId: e.target.value }))
                }
                className="text-xs font-mono"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Correlation ID
              </label>
              <Input
                placeholder="Tracking UUID"
                value={draftFilters.correlationId}
                onChange={(e) =>
                  setDraftFilters((prev) => ({ ...prev, correlationId: e.target.value }))
                }
                className="text-xs font-mono"
              />
            </div>
          </div>

          <div className="flex items-center justify-between pt-2 border-t border-slate-100">
            <div className="flex items-center gap-2">
              <Button type="submit" variant="primary" size="sm" className="gap-1.5">
                <Search className="w-3.5 h-3.5" /> Apply Filters
              </Button>
              {hasFilterValues && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={handleClearFilters}
                  className="gap-1 text-slate-500"
                >
                  <X className="w-3.5 h-3.5" /> Clear Filters
                </Button>
              )}
            </div>

            <div className="flex items-center gap-2 text-xs text-slate-600">
              <span>Page Size:</span>
              <select
                value={limit}
                onChange={(e) => {
                  setLimit(Number(e.target.value));
                  setOffset(0);
                }}
                className="px-2 py-1 text-xs border border-slate-200 rounded bg-white text-slate-800"
              >
                <option value={10}>10</option>
                <option value={25}>25</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
              </select>
            </div>
          </div>
        </form>
      </Card>

      {/* Main Audit Table Card */}
      <Card className="border-slate-200 shadow-sm bg-white">
        <CardHeader className="border-b border-slate-100 pb-3 flex flex-row items-center justify-between">
          <CardTitle className="text-base font-semibold text-slate-900 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-indigo-600" /> Chronological Audit Log
          </CardTitle>
          <span className="text-xs text-slate-500">
            Showing {events.length} records (Page {currentPage})
          </span>
        </CardHeader>

        <CardContent className="p-0">
          {loading ? (
            <div className="p-12">
              <LoadingState message="Loading immutable audit trail records..." />
            </div>
          ) : error ? (
            <div className="p-6">
              <ErrorState
                title="Audit Query Failed"
                message={error}
                onRetry={fetchAuditEvents}
              />
            </div>
          ) : events.length === 0 ? (
            <div className="p-12">
              <EmptyState
                icon={<ShieldCheck className="w-12 h-12 text-slate-300" />}
                title="No Audit Events Found"
                description="No matching audit trail records were found for the current query parameters and tenant partition."
                actionLabel={hasFilterValues ? "Reset Filters" : undefined}
                onAction={hasFilterValues ? handleClearFilters : undefined}
              />
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Timestamp</TableHead>
                    <TableHead>Event Type</TableHead>
                    <TableHead>Outcome</TableHead>
                    <TableHead>Resource</TableHead>
                    <TableHead>Actor &amp; Auth</TableHead>
                    <TableHead>Correlation ID</TableHead>
                    <TableHead className="text-right">Details</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {events.map((ev) => (
                    <TableRow key={ev.audit_event_id}>
                      <TableCell className="text-xs text-slate-600 whitespace-nowrap font-medium">
                        {formatDateTime(ev.recorded_at)}
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-xs font-semibold text-indigo-900 bg-indigo-50/80 px-2 py-0.5 rounded border border-indigo-100">
                          {ev.event_type}
                        </span>
                      </TableCell>
                      <TableCell>{getOutcomeBadge(ev.outcome)}</TableCell>
                      <TableCell className="text-xs text-slate-700">
                        <span className="font-semibold uppercase tracking-wider text-[11px] text-slate-500 mr-1">
                          {ev.resource_type || "GLOBAL"}
                        </span>
                        {ev.resource_id && (
                          <span className="font-mono text-[11px] text-slate-800" title={ev.resource_id}>
                            {formatShortId(ev.resource_id)}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-slate-600">
                        {ev.actor_principal_id ? (
                          <div>
                            <span className="font-medium text-slate-800 flex items-center gap-1">
                              <User className="w-3 h-3 text-slate-400" />
                              {ev.actor_principal_id}
                            </span>
                            <span className="text-[10px] text-slate-400">
                              {ev.actor_principal_type} • {ev.authentication_method}
                            </span>
                          </div>
                        ) : (
                          <span className="text-slate-400 italic">System Actor</span>
                        )}
                      </TableCell>
                      <TableCell className="text-xs font-mono text-slate-400">
                        {ev.correlation_id ? formatShortId(ev.correlation_id) : "-"}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => openEventDetails(ev)}
                          className="h-7 px-2 text-xs text-indigo-600 hover:text-indigo-800"
                        >
                          <Eye className="w-3.5 h-3.5 mr-1" /> Inspect
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {/* Pagination Footer */}
              <div className="flex items-center justify-between px-4 py-3 border-t border-slate-100 bg-slate-50/50">
                <span className="text-xs text-slate-500">
                  Showing offset {offset} - {offset + events.length}
                </span>
                <div className="flex items-center gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setOffset(Math.max(0, offset - limit))}
                    disabled={!hasPrevPage || loading}
                    className="h-8 gap-1"
                  >
                    <ChevronLeft className="w-3.5 h-3.5" /> Previous
                  </Button>
                  <span className="text-xs font-medium text-slate-700 px-2">
                    Page {currentPage}
                  </span>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setOffset(offset + limit)}
                    disabled={!hasNextPage || loading}
                    className="h-8 gap-1"
                  >
                    Next <ChevronRight className="w-3.5 h-3.5" />
                  </Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Details Inspection Modal */}
      {selectedEvent && (
        <Modal
          isOpen={isDetailModalOpen}
          onClose={() => setIsDetailModalOpen(false)}
          title={`Audit Record: ${selectedEvent.event_type}`}
        >
          <div className="space-y-4 text-left">
            {/* Meta Grid */}
            <div className="grid grid-cols-2 gap-3 text-xs bg-slate-50 p-3 rounded-lg border border-slate-100">
              <div>
                <span className="text-slate-400 block">Audit Event ID</span>
                <span className="font-mono text-slate-800">{selectedEvent.audit_event_id}</span>
              </div>
              <div>
                <span className="text-slate-400 block">Recorded Timestamp</span>
                <span className="text-slate-800">{formatDateTime(selectedEvent.recorded_at)}</span>
              </div>
              <div>
                <span className="text-slate-400 block">Outcome</span>
                <span>{getOutcomeBadge(selectedEvent.outcome)}</span>
              </div>
              <div>
                <span className="text-slate-400 block">Tenant Partition</span>
                <span className="font-semibold text-indigo-700">{selectedEvent.tenant_id}</span>
              </div>
              <div>
                <span className="text-slate-400 block">Resource Classification</span>
                <span className="text-slate-800 font-medium">
                  {selectedEvent.resource_type || "N/A"}: {selectedEvent.resource_id || "N/A"}
                </span>
              </div>
              <div>
                <span className="text-slate-400 block">Correlation Root ID</span>
                <span className="font-mono text-slate-700">{selectedEvent.correlation_id || "None"}</span>
              </div>
            </div>

            {/* Sanitized & Redacted Details JSON */}
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1.5 flex items-center gap-1.5">
                <Lock className="w-3.5 h-3.5 text-slate-400" /> Immutable Event Payload (Display Redacted)
              </label>
              <pre className="p-4 bg-slate-900 text-indigo-200 text-xs font-mono rounded-lg overflow-x-auto max-h-72 shadow-inner">
                {JSON.stringify(redactSensitiveForDisplay(selectedEvent.details), null, 2)}
              </pre>
            </div>

            <div className="flex justify-end pt-2">
              <Button variant="secondary" onClick={() => setIsDetailModalOpen(false)}>
                Close
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
};
