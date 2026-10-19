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
} from "lucide-react";
import { apiClient } from "../api/client";
import type { ResourceCatalogItem, ResourceType } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { Input } from "../components/ui/Input";
import { Select } from "../components/ui/Select";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "../components/ui/Table";
import { LoadingState } from "../components/ui/LoadingState";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";

export const ResourcesPage: React.FC = () => {
  const { user } = useAuth();

  const [resources, setResources] = useState<ResourceCatalogItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [typeFilter, setTypeFilter] = useState<string>("ALL");
  const [activeFilter, setActiveFilter] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");

  const tenantId = user?.tenant_id || "tenant-engineering";

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
              <Button variant="primary" size="sm" className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" />
                Availability Windows
              </Button>
            </Link>
          </div>
        }
      />

      {/* Summary KPI Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Card className="p-3.5 flex items-center justify-between">
          <div>
            <span className="text-[11px] text-slate-400">Total Resources</span>
            <div className="text-xl font-bold text-white">{totalCount}</div>
          </div>
          <span className="p-2 rounded-lg bg-indigo-500/10 text-indigo-400">
            <Users className="w-4 h-4" />
          </span>
        </Card>

        <Card className="p-3.5 flex items-center justify-between">
          <div>
            <span className="text-[11px] text-slate-400">Personnel / Invigilators</span>
            <div className="text-xl font-bold text-blue-400">{personCount}</div>
          </div>
          <span className="p-2 rounded-lg bg-blue-500/10 text-blue-400">
            <Users className="w-4 h-4" />
          </span>
        </Card>

        <Card className="p-3.5 flex items-center justify-between">
          <div>
            <span className="text-[11px] text-slate-400">Rooms & Halls</span>
            <div className="text-xl font-bold text-emerald-400">{roomCount}</div>
          </div>
          <span className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400">
            <Building className="w-4 h-4" />
          </span>
        </Card>

        <Card className="p-3.5 flex items-center justify-between">
          <div>
            <span className="text-[11px] text-slate-400">Equipment & Devices</span>
            <div className="text-xl font-bold text-amber-400">{equipCount}</div>
          </div>
          <span className="p-2 rounded-lg bg-amber-500/10 text-amber-400">
            <Wrench className="w-4 h-4" />
          </span>
        </Card>
      </div>

      {/* Search & Filters */}
      <Card className="p-4 flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="w-full md:w-80 relative">
          <Search className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search resources, keys, or skills..."
            className="pl-9"
          />
        </div>

        <div className="flex items-center gap-3 w-full md:w-auto">
          <div className="w-44">
            <Select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              options={[
                { value: "ALL", label: "All Categories" },
                { value: "PERSON", label: "Personnel / Staff" },
                { value: "ROOM", label: "Halls & Rooms" },
                { value: "EQUIPMENT", label: "Equipment" },
              ]}
            />
          </div>

          <div className="w-36">
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
      </Card>

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
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-44">Resource Key</TableHead>
              <TableHead>Display Label</TableHead>
              <TableHead className="w-32">Category</TableHead>
              <TableHead className="w-24">Capacity</TableHead>
              <TableHead>Skills & Capabilities</TableHead>
              <TableHead className="w-24">Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filteredResources.map((res) => (
              <TableRow key={res.id}>
                <TableCell className="font-mono text-xs font-semibold text-slate-300">
                  {res.resource_key}
                </TableCell>

                <TableCell>
                  <div className="flex items-center gap-2">
                    <span className="p-1 rounded bg-slate-800">
                      {getResourceTypeIcon(res.type)}
                    </span>
                    <span className="font-semibold text-slate-100">{res.display_label}</span>
                  </div>
                </TableCell>

                <TableCell>{getResourceTypeBadge(res.type)}</TableCell>

                <TableCell className="font-semibold text-slate-200">{res.capacity}</TableCell>

                <TableCell>
                  {res.skills && res.skills.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {res.skills.map((skill, idx) => (
                        <span
                          key={idx}
                          className="px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-300 text-[10px] font-mono"
                        >
                          {skill}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span className="text-slate-400 text-xs">—</span>
                  )}
                </TableCell>

                <TableCell>
                  {res.active ? (
                    <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-400">
                      <CheckCircle className="w-3.5 h-3.5" /> ACTIVE
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-400">
                      <XCircle className="w-3.5 h-3.5" /> INACTIVE
                    </span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
};
