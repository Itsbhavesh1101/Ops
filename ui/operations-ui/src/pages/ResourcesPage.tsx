import React, { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  Users,
  Building,
  Wrench,
  Search,
  RefreshCw,
  Clock,
  CheckCircle,
  XCircle,
  HelpCircle,
  Plus,
} from "lucide-react";
import { apiClient } from "../api/client";
import type { ResourceCatalogItem, ResourceType } from "../api/types";
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

export const ResourcesPage: React.FC = () => {
  const { user } = useAuth();
  const { showToast } = useToast();

  const [resources, setResources] = useState<ResourceCatalogItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [typeFilter, setTypeFilter] = useState<string>("ALL");
  const [activeFilter, setActiveFilter] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");

  // Add Resource Modal State
  const [isAddModalOpen, setIsAddModalOpen] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [resourceKey, setResourceKey] = useState<string>("");
  const [displayLabel, setDisplayLabel] = useState<string>("");
  const [resourceType, setResourceType] = useState<ResourceType>("ROOM");
  const [capacity, setCapacity] = useState<number>(80);
  const [skillsInput, setSkillsInput] = useState<string>("");

  const tenantId = user?.tenant_id || "tenant-engineering";

  const handleCreateResource = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resourceKey.trim() || !displayLabel.trim()) {
      setSubmitError("Resource Key and Display Label are required.");
      return;
    }
    if (resourceType === "ROOM" && capacity < 1) {
      setSubmitError("ROOM capacity must be at least 1.");
      return;
    }

    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const skills = skillsInput
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.length > 0);

      await apiClient.createResource(
        {
          resource_key: resourceKey.trim(),
          display_label: displayLabel.trim(),
          type: resourceType,
          capacity: Number(capacity),
          skills: skills.length > 0 ? skills : undefined,
          active: true,
        },
        tenantId
      );

      showToast("success", `Resource '${displayLabel}' registered successfully.`, "Resource Created");
      setIsAddModalOpen(false);
      setResourceKey("");
      setDisplayLabel("");
      setResourceType("ROOM");
      setCapacity(80);
      setSkillsInput("");
      fetchResources();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to create resource";
      setSubmitError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const fetchResources = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiClient.listResources(
        {
          type: typeFilter !== "ALL" ? typeFilter : undefined,
          active_only: activeFilter === "ACTIVE" ? true : undefined,
        },
        tenantId
      );
      setResources(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load resource catalog";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [typeFilter, activeFilter, tenantId]);

  useEffect(() => {
    fetchResources();
  }, [fetchResources]);

  const filteredResources = resources.filter((res) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      res.display_label.toLowerCase().includes(q) ||
      res.resource_key.toLowerCase().includes(q) ||
      (res.skills && res.skills.some((s) => s.toLowerCase().includes(q)))
    );
  });

  const getResourceTypeIcon = (type: ResourceType | string) => {
    switch (type) {
      case "PERSON":
        return <Users className="w-3.5 h-3.5 text-blue-400" />;
      case "ROOM":
        return <Building className="w-3.5 h-3.5 text-emerald-400" />;
      case "EQUIPMENT":
        return <Wrench className="w-3.5 h-3.5 text-amber-400" />;
      default:
        return <HelpCircle className="w-3.5 h-3.5 text-purple-400" />;
    }
  };

  const getResourceTypeBadge = (type: ResourceType | string) => {
    switch (type) {
      case "PERSON":
        return <Badge variant="info">PERSON</Badge>;
      case "ROOM":
        return <Badge variant="success">ROOM</Badge>;
      case "EQUIPMENT":
        return <Badge variant="warning">EQUIPMENT</Badge>;
      default:
        return <Badge variant="neutral">{type}</Badge>;
    }
  };

  // Metric counts
  const totalCount = resources.length;
  const personCount = resources.filter((r) => r.type === "PERSON").length;
  const roomCount = resources.filter((r) => r.type === "ROOM").length;
  const equipCount = resources.filter((r) => r.type === "EQUIPMENT").length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Resource Catalog"
        description="Enterprise workforce, examination halls, laboratories, and physical equipment inventory."
        actions={
          <div className="flex items-center gap-3">
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchResources}
              disabled={loading}
              className="flex items-center gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            <Link to="/resources/availability">
              <Button variant="secondary" size="sm" className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" />
                Availability Windows
              </Button>
            </Link>
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                setSubmitError(null);
                setIsAddModalOpen(true);
              }}
              className="flex items-center gap-1.5"
            >
              <Plus className="w-3.5 h-3.5" />
              Add Resource
            </Button>
          </div>
        }
      />

      {/* Compact Inline Summary Metrics */}
      <div className="flex items-center gap-3 px-3.5 py-2 rounded-lg bg-slate-50/80 border border-slate-200/80 text-xs text-slate-600">
        <span className="font-semibold text-slate-900">{totalCount} {totalCount === 1 ? "Resource" : "Resources"}</span>
        <span className="text-slate-300">•</span>
        <span>{roomCount} {roomCount === 1 ? "Room" : "Rooms"}</span>
        <span className="text-slate-300">•</span>
        <span>{personCount} Personnel</span>
        <span className="text-slate-300">•</span>
        <span>{equipCount} Equipment</span>
      </div>

      {/* Compact Inline Search & Filters Toolbar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-2.5" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search resources, keys, or skills..."
            className="pl-8 text-xs h-9"
          />
        </div>

        <div className="flex items-center gap-2">
          <div className="w-36">
            <Select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              options={[
                { value: "ALL", label: "All Types" },
                { value: "ROOM", label: "Rooms & Halls" },
                { value: "PERSON", label: "Personnel / Staff" },
                { value: "EQUIPMENT", label: "Equipment" },
              ]}
            />
          </div>

          <div className="w-32">
            <Select
              value={activeFilter}
              onChange={(e) => setActiveFilter(e.target.value)}
              options={[
                { value: "ALL", label: "All Statuses" },
                { value: "ACTIVE", label: "Active Only" },
              ]}
            />
          </div>
        </div>
      </div>

      {loading && <LoadingState message="Fetching resource catalog from database..." />}

      {error && !loading && (
        <ErrorState
          title="Failed to Load Resources"
          message={error}
          onRetry={fetchResources}
        />
      )}

      {!loading && !error && filteredResources.length === 0 && (
        <EmptyState
          title="No Resources Found"
          description={
            searchQuery
              ? "No resources match your search criteria."
              : "No resources found in the engine catalog."
          }
        />
      )}

      {!loading && !error && filteredResources.length > 0 && (
        <div className="bg-white border border-slate-200/80 rounded-xl overflow-hidden shadow-2xs">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50/70 border-b border-slate-200/80">
                <TableHead>Resource</TableHead>
                <TableHead className="w-28">Type</TableHead>
                <TableHead className="w-24">Capacity</TableHead>
                <TableHead className="w-32">Availability</TableHead>
                <TableHead className="w-24">Source</TableHead>
                <TableHead className="w-24">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredResources.map((res) => {
                const isLocal =
                  res.attributes?.source === "MANUAL_OPERATIONAL" ||
                  res.attributes?.provider_type === "LOCAL" ||
                  res.type !== "PERSON";
                return (
                  <TableRow key={res.id} className="hover:bg-slate-50/50 transition-colors">
                    <TableCell>
                      <div className="flex items-start gap-2.5">
                        <span className="p-1.5 rounded-md bg-slate-100 text-slate-600 shrink-0 mt-0.5">
                          {getResourceTypeIcon(res.type)}
                        </span>
                        <div>
                          <div className="font-semibold text-slate-900 text-xs leading-snug">
                            {res.display_label}
                          </div>
                          <div className="font-mono text-[11px] text-slate-500 mt-0.5">
                            {res.resource_key}
                          </div>
                          {res.skills && res.skills.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-1.5">
                              {res.skills.map((skill, idx) => (
                                <span
                                  key={idx}
                                  className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 text-[10px] font-medium"
                                >
                                  {skill.replace(/[_-]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    </TableCell>

                    <TableCell>{getResourceTypeBadge(res.type)}</TableCell>

                    <TableCell className="font-semibold text-slate-900 tabular-nums text-xs">
                      {res.capacity}
                    </TableCell>

                    <TableCell>
                      <Link
                        to={`/resources/availability?resource_id=${res.id}`}
                        className="text-xs text-indigo-600 hover:text-indigo-800 font-medium inline-flex items-center gap-1 transition-colors"
                      >
                        <Clock className="w-3 h-3" />
                        View Windows
                      </Link>
                    </TableCell>

                    <TableCell>
                      {isLocal ? (
                        <Badge variant="neutral">Local</Badge>
                      ) : (
                        <Badge variant="info">ERP</Badge>
                      )}
                    </TableCell>

                    <TableCell>
                      {res.active ? (
                        <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-600">
                          <CheckCircle className="w-3.5 h-3.5" /> Active
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-400">
                          <XCircle className="w-3.5 h-3.5" /> Inactive
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Add Resource Modal */}
      <Modal
        isOpen={isAddModalOpen}
        onClose={() => !isSubmitting && setIsAddModalOpen(false)}
        title="Add Local Operational Resource"
        description="Register a new room, examination hall, laboratory, or physical equipment."
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
              onClick={handleCreateResource}
              disabled={isSubmitting}
              className="flex items-center gap-1.5"
            >
              {isSubmitting ? (
                <>
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  Creating...
                </>
              ) : (
                <>
                  <Plus className="w-3.5 h-3.5" />
                  Create Resource
                </>
              )}
            </Button>
          </div>
        }
      >
        <form onSubmit={handleCreateResource} className="space-y-4">
          {submitError && (
            <div className="p-3 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs">
              {submitError}
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1">
              Resource Key <span className="text-rose-500">*</span>
            </label>
            <Input
              value={resourceKey}
              onChange={(e) => setResourceKey(e.target.value)}
              placeholder="e.g., room-hall-a"
              required
              disabled={isSubmitting}
            />
            <p className="text-[11px] text-slate-400 mt-1">Unique alphanumeric identifier for solver matching.</p>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1">
              Display Label / Name <span className="text-rose-500">*</span>
            </label>
            <Input
              value={displayLabel}
              onChange={(e) => setDisplayLabel(e.target.value)}
              placeholder="e.g., Examination Hall A"
              required
              disabled={isSubmitting}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Resource Category <span className="text-rose-500">*</span>
              </label>
              <Select
                value={resourceType}
                onChange={(e) => setResourceType(e.target.value as ResourceType)}
                disabled={isSubmitting}
                options={[
                  { value: "ROOM", label: "ROOM (Examination Hall / Lab)" },
                  { value: "EQUIPMENT", label: "EQUIPMENT (Physical Device)" },
                  { value: "OTHER", label: "OTHER (Facility Resource)" },
                ]}
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Capacity <span className="text-rose-500">*</span>
              </label>
              <Input
                type="number"
                min="1"
                value={capacity}
                onChange={(e) => setCapacity(parseInt(e.target.value, 10) || 1)}
                required
                disabled={isSubmitting}
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1">
              Skills / Capabilities (Comma-separated)
            </label>
            <Input
              value={skillsInput}
              onChange={(e) => setSkillsInput(e.target.value)}
              placeholder="e.g., cctv_monitored, projector, sound_system"
              disabled={isSubmitting}
            />
          </div>

          <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200 text-[11px] text-slate-500">
            <strong>Note:</strong> Personnel and staff resources are synchronized authoritatively from ERP and cannot be created as local operator items.
          </div>
        </form>
      </Modal>
    </div>
  );
};
