import React, { useState, useEffect, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import {
  GitBranch,
  ArrowLeft,
  Clock,
  CheckCircle2,
  AlertTriangle,
  PlayCircle,
  PauseCircle,
  XCircle,
  RefreshCw,
  Hash,
  User,
  Inbox,
  FileCode,
  Activity,
  ShieldAlert,
} from "lucide-react";
import { apiClient, ApiError } from "../api/client";
import type { WorkflowTask } from "../api/client";
import { formatDateTime, formatShortId } from "../utils/formatters";
import { redactSensitiveForDisplay } from "../utils/redact";
import type {
  WorkflowInstanceResponse,
  WorkflowHistoryEntry,
} from "../api/types";
import { useAuth } from "../context/AuthContext";
import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
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
import { LoadingState } from "../components/ui/LoadingState";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";

export const WorkflowInstancePage: React.FC = () => {
  const { instanceId } = useParams<{ instanceId: string }>();
  const { user } = useAuth();

  const [instance, setInstance] = useState<WorkflowInstanceResponse | null>(null);
  const [history, setHistory] = useState<WorkflowHistoryEntry[]>([]);
  const [tasks, setTasks] = useState<WorkflowTask[]>([]);
  
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<number | undefined>(undefined);

  // Partial failure states for secondary data streams
  const [historyLoading, setHistoryLoading] = useState<boolean>(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyErrorCode, setHistoryErrorCode] = useState<number | undefined>(undefined);

  const [tasksLoading, setTasksLoading] = useState<boolean>(false);
  const [tasksError, setTasksError] = useState<string | null>(null);
  const [tasksErrorCode, setTasksErrorCode] = useState<number | undefined>(undefined);

  const [activeTab, setActiveTab] = useState<"history" | "tasks" | "variables">("history");

  const tenantId = user?.tenant_id;

  const activeInstanceIdRef = React.useRef<string | undefined>(instanceId);
  const activeTenantIdRef = React.useRef<string | undefined>(tenantId);

  useEffect(() => {
    activeInstanceIdRef.current = instanceId;
    activeTenantIdRef.current = tenantId;
  }, [instanceId, tenantId]);

  const classifyError = (err: unknown, entityName: string) => {
    let msg = `Failed to load ${entityName}`;
    let code: number | undefined;

    if (err instanceof ApiError) {
      code = err.status;
      if (err.status === 403) {
        msg = `Forbidden (HTTP 403): You do not have permission to view ${entityName} for this workflow instance.`;
      } else if (err.status === 404) {
        msg = `Not Found (HTTP 404): The requested ${entityName} was not found.`;
      } else if (err.status >= 500) {
        msg = `Server Error (HTTP ${err.status}): The workflow engine encountered an internal error while retrieving ${entityName}.`;
      } else {
        msg = err.message || `API Error (HTTP ${err.status}): Failed to retrieve ${entityName}.`;
      }
    } else if (err instanceof Error) {
      if (err.message.toLowerCase().includes("failed to fetch") || err.message.toLowerCase().includes("network")) {
        msg = `Network Error: Unable to communicate with the workflow API to retrieve ${entityName}. Please verify your connectivity.`;
      } else {
        msg = err.message;
      }
    }
    return { msg, code };
  };

  const fetchHistory = useCallback(async (targetInstanceId: string, targetTenantId: string) => {
    if (!targetInstanceId || !targetTenantId) return;
    setHistoryLoading(true);
    setHistoryError(null);
    setHistoryErrorCode(undefined);
    try {
      const histData = await apiClient.getWorkflowHistory(targetInstanceId, targetTenantId);
      if (activeInstanceIdRef.current !== targetInstanceId || activeTenantIdRef.current !== targetTenantId) {
        return; // Stale response discarded
      }
      const normalizedHistory: WorkflowHistoryEntry[] = Array.isArray(histData)
        ? histData
        : histData && Array.isArray((histData as { activities?: WorkflowHistoryEntry[] }).activities)
        ? (histData as { activities: WorkflowHistoryEntry[] }).activities
        : [];
      setHistory(normalizedHistory);
    } catch (err: unknown) {
      if (activeInstanceIdRef.current !== targetInstanceId || activeTenantIdRef.current !== targetTenantId) {
        return; // Stale response discarded
      }
      const { msg, code } = classifyError(err, "activity execution history");
      setHistoryError(msg);
      setHistoryErrorCode(code);
    } finally {
      if (activeInstanceIdRef.current === targetInstanceId && activeTenantIdRef.current === targetTenantId) {
        setHistoryLoading(false);
      }
    }
  }, []);

  const fetchTasks = useCallback(async (targetInstanceId: string, targetTenantId: string) => {
    if (!targetInstanceId || !targetTenantId) return;
    setTasksLoading(true);
    setTasksError(null);
    setTasksErrorCode(undefined);
    try {
      const taskData = await apiClient.listWorkflowTasks(targetTenantId, { instance_id: targetInstanceId });
      if (activeInstanceIdRef.current !== targetInstanceId || activeTenantIdRef.current !== targetTenantId) {
        return; // Stale response discarded
      }
      setTasks(taskData);
    } catch (err: unknown) {
      if (activeInstanceIdRef.current !== targetInstanceId || activeTenantIdRef.current !== targetTenantId) {
        return; // Stale response discarded
      }
      const { msg, code } = classifyError(err, "workflow human tasks");
      setTasksError(msg);
      setTasksErrorCode(code);
    } finally {
      if (activeInstanceIdRef.current === targetInstanceId && activeTenantIdRef.current === targetTenantId) {
        setTasksLoading(false);
      }
    }
  }, []);

  const fetchInstanceData = useCallback(async () => {
    if (!instanceId) return;
    activeInstanceIdRef.current = instanceId;
    activeTenantIdRef.current = tenantId;

    if (!user) {
      setError("Authentication session required. Please sign in to inspect workflow instances.");
      setInstance(null);
      setHistory([]);
      setTasks([]);
      setLoading(false);
      return;
    }
    if (!tenantId) {
      setError("Active authenticated tenant context is required to inspect workflow instances.");
      setInstance(null);
      setHistory([]);
      setTasks([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    setErrorCode(undefined);
    setHistory([]);
    setTasks([]);
    setHistoryError(null);
    setHistoryErrorCode(undefined);
    setTasksError(null);
    setTasksErrorCode(undefined);

    const currentInstanceId = instanceId;
    const currentTenantId = tenantId;

    try {
      const instData = await apiClient.getWorkflowInstance(currentInstanceId, currentTenantId);

      // Check if instance or tenant context shifted while request was in-flight
      if (activeInstanceIdRef.current !== currentInstanceId || activeTenantIdRef.current !== currentTenantId) {
        return; // Discard stale response
      }

      setInstance(instData);
      setLoading(false);

      // Primary instance succeeded (HTTP 200) -> Dispatch secondary streams
      fetchHistory(currentInstanceId, currentTenantId);
      fetchTasks(currentInstanceId, currentTenantId);
    } catch (err: unknown) {
      // Discard stale failure if user navigated away
      if (activeInstanceIdRef.current !== currentInstanceId || activeTenantIdRef.current !== currentTenantId) {
        return;
      }
      const { msg, code } = classifyError(err, "workflow instance");
      setError(msg);
      setErrorCode(code);
      setInstance(null);
      setHistory([]);
      setTasks([]);
      setLoading(false);
      // Secondary requests MUST NOT execute when primary instance retrieval fails
    }
  }, [instanceId, user, tenantId, fetchHistory, fetchTasks]);

  const handleRetryHistory = useCallback(() => {
    if (instanceId && tenantId) {
      fetchHistory(instanceId, tenantId);
    }
  }, [instanceId, tenantId, fetchHistory]);

  const handleRetryTasks = useCallback(() => {
    if (instanceId && tenantId) {
      fetchTasks(instanceId, tenantId);
    }
  }, [instanceId, tenantId, fetchTasks]);

  useEffect(() => {
    fetchInstanceData();
  }, [fetchInstanceData]);

  const getStatusBadge = (status: string) => {
    switch (status.toUpperCase()) {
      case "RUNNING":
        return (
          <Badge variant="info" className="flex items-center gap-1">
            <PlayCircle className="w-3 h-3" /> Running
          </Badge>
        );
      case "COMPLETED":
        return (
          <Badge variant="success" className="flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3" /> Completed
          </Badge>
        );
      case "SUSPENDED":
        return (
          <Badge variant="warning" className="flex items-center gap-1">
            <PauseCircle className="w-3 h-3" /> Suspended
          </Badge>
        );
      case "TERMINATED":
      case "FAILED":
        return (
          <Badge variant="error" className="flex items-center gap-1">
            <XCircle className="w-3 h-3" /> {status}
          </Badge>
        );
      default:
        return <Badge variant="neutral">{status}</Badge>;
    }
  };

  const humanizeActivityType = (type: string): string => {
    switch (type) {
      case "startEvent":
        return "Process Start";
      case "endEvent":
        return "Process End";
      case "userTask":
        return "Human User Task";
      case "serviceTask":
        return "Automated Service Task";
      case "exclusiveGateway":
        return "Decision Gateway (XOR)";
      case "parallelGateway":
        return "Parallel Split/Join";
      default:
        return type.replace(/([A-Z])/g, " $1").replace(/^./, (str) => str.toUpperCase());
    }
  };

  if (loading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Workflow Instance"
          description="Retrieving execution projection and normalized activity history..."
          breadcrumbs={[
            { label: "Dashboard", href: "/dashboard" },
            { label: "Workflow Inbox", href: "/workflows/inbox" },
            { label: instanceId ? formatShortId(instanceId) : "Instance" },
          ]}
        />
        <LoadingState message="Loading workflow instance execution details..." />
      </div>
    );
  }

  if (error || !instance) {
    if (errorCode === 404) {
      return (
        <div className="space-y-6">
          <PageHeader
            title="Workflow Instance Not Found"
            breadcrumbs={[
              { label: "Dashboard", href: "/dashboard" },
              { label: "Workflow Inbox", href: "/workflows/inbox" },
              { label: "Not Found" },
            ]}
          />
          <EmptyState
            icon={<XCircle className="w-12 h-12 text-rose-400" />}
            title="Instance Not Found"
            description={`Workflow instance "${instanceId}" was not found in tenant "${tenantId}". It may have expired or belongs to another partition.`}
            actionLabel="Back to Workflow Inbox"
            onAction={() => window.location.assign("/workflows/inbox")}
          />
        </div>
      );
    }

    if (errorCode === 403) {
      return (
        <div className="space-y-6">
          <PageHeader
            title="Access Forbidden"
            breadcrumbs={[
              { label: "Dashboard", href: "/dashboard" },
              { label: "Workflow Inbox", href: "/workflows/inbox" },
              { label: "Forbidden" },
            ]}
          />
          <EmptyState
            icon={<AlertTriangle className="w-12 h-12 text-amber-400" />}
            title="Tenant Boundary Enforced"
            description={`You do not have permission to inspect workflow instances outside tenant "${tenantId}". Cross-tenant execution inspection is strictly prohibited.`}
            actionLabel="Back to Inbox"
            onAction={() => window.location.assign("/workflows/inbox")}
          />
        </div>
      );
    }

    return (
      <div className="space-y-6">
        <PageHeader
          title="Workflow Instance"
          breadcrumbs={[
            { label: "Dashboard", href: "/dashboard" },
            { label: "Workflow Inbox", href: "/workflows/inbox" },
            { label: "Error" },
          ]}
        />
        <ErrorState
          title="Failed to Load Instance"
          message={error || "An unexpected error occurred while loading the workflow instance."}
          onRetry={fetchInstanceData}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6 text-left">
      {/* Page Header */}
      <PageHeader
        title={instance.workflow_key}
        description={`Canonical execution instance v${instance.version} under tenant ${instance.tenant_id}`}
        badge={getStatusBadge(instance.status)}
        breadcrumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "Workflow Inbox", href: "/workflows/inbox" },
          { label: instance.workflow_key, href: "/workflows/inbox" },
          { label: formatShortId(instance.id) },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <Link to="/workflows/inbox">
              <Button variant="secondary" size="sm" className="gap-1.5">
                <ArrowLeft className="w-4 h-4" /> Workflow Inbox
              </Button>
            </Link>
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchInstanceData}
              disabled={loading}
              className="gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
            </Button>
          </div>
        }
      />

      {/* Partial Data Degradation Alert */}
      {(historyError || tasksError) && (
        <div className="p-3.5 bg-amber-50 border border-amber-200 text-amber-900 rounded-lg text-xs flex items-center justify-between gap-2 shadow-xs">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
            <span>
              <strong>Partial Data Warning:</strong> One or more secondary data streams failed to load (
              {historyError && "Activity History"}
              {historyError && tasksError && ", "}
              {tasksError && "Human Tasks"}
              ). Instance metadata is safely displayed below. Select the respective tab to retry.
            </span>
          </div>
        </div>
      )}

      {/* Metadata Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="p-4 border-slate-200 shadow-sm bg-white">
          <div className="flex items-center gap-2 text-slate-500 text-xs font-semibold uppercase tracking-wider mb-1">
            <GitBranch className="w-3.5 h-3.5 text-indigo-600" /> Workflow Key
          </div>
          <div className="text-base font-semibold text-slate-900 truncate">
            {instance.workflow_key}
          </div>
          <div className="text-xs text-slate-500 mt-1">
            Version <span className="font-semibold text-indigo-600">v{instance.version}</span>
          </div>
        </Card>

        <Card className="p-4 border-slate-200 shadow-sm bg-white">
          <div className="flex items-center gap-2 text-slate-500 text-xs font-semibold uppercase tracking-wider mb-1">
            <Hash className="w-3.5 h-3.5 text-indigo-600" /> Instance UUID
          </div>
          <div className="text-xs font-mono font-medium text-slate-800 truncate" title={instance.id}>
            {instance.id}
          </div>
          <div className="text-xs text-slate-400 mt-1 truncate" title={instance.engine_instance_id}>
            Engine Ref: {instance.engine_instance_id}
          </div>
        </Card>

        <Card className="p-4 border-slate-200 shadow-sm bg-white">
          <div className="flex items-center gap-2 text-slate-500 text-xs font-semibold uppercase tracking-wider mb-1">
            <Clock className="w-3.5 h-3.5 text-indigo-600" /> Started
          </div>
          <div className="text-xs font-medium text-slate-800">
            {formatDateTime(instance.started_at)}
          </div>
          <div className="text-xs text-slate-500 mt-1 flex items-center gap-1 truncate">
            <User className="w-3 h-3" /> {instance.started_by || "System Automated"}
          </div>
        </Card>

        <Card className="p-4 border-slate-200 shadow-sm bg-white">
          <div className="flex items-center gap-2 text-slate-500 text-xs font-semibold uppercase tracking-wider mb-1">
            <CheckCircle2 className="w-3 h-3.5 text-emerald-600" /> Completion
          </div>
          <div className="text-xs font-medium text-slate-800">
            {instance.completed_at ? formatDateTime(instance.completed_at) : "In Progress"}
          </div>
          {instance.business_reference && (
            <div className="text-xs text-indigo-600 mt-1 truncate" title={instance.business_reference}>
              Ref: {instance.business_reference}
            </div>
          )}
        </Card>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-slate-200 space-x-6 text-sm font-medium">
        <button
          onClick={() => setActiveTab("history")}
          className={`pb-3 flex items-center gap-2 border-b-2 transition-colors ${
            activeTab === "history"
              ? "border-indigo-600 text-indigo-600 font-semibold"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <Activity className="w-4 h-4" /> Activity History ({history.length})
          {historyError && (
            <span className="w-2 h-2 rounded-full bg-rose-500 inline-block" title="Error loading history" />
          )}
        </button>
        <button
          onClick={() => setActiveTab("tasks")}
          className={`pb-3 flex items-center gap-2 border-b-2 transition-colors ${
            activeTab === "tasks"
              ? "border-indigo-600 text-indigo-600 font-semibold"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <Inbox className="w-4 h-4" /> Human Tasks ({tasks.length})
          {tasksError && (
            <span className="w-2 h-2 rounded-full bg-rose-500 inline-block" title="Error loading tasks" />
          )}
        </button>
        <button
          onClick={() => setActiveTab("variables")}
          className={`pb-3 flex items-center gap-2 border-b-2 transition-colors ${
            activeTab === "variables"
              ? "border-indigo-600 text-indigo-600 font-semibold"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          <FileCode className="w-4 h-4" /> Process Variables (
          {Object.keys(instance.input_variables || {}).length})
        </button>
      </div>

      {/* Tab 1: Execution Activity History */}
      {activeTab === "history" && (
        <Card className="border-slate-200 shadow-sm bg-white">
          <CardHeader className="pb-3 border-b border-slate-100 flex flex-row items-center justify-between">
            <CardTitle className="text-base font-semibold text-slate-900 flex items-center gap-2">
              <Activity className="w-4 h-4 text-indigo-600" /> Normalized Activity Execution Trail
            </CardTitle>
            <span className="text-xs text-slate-500">
              Deterministic sequence from runtime audit projection
            </span>
          </CardHeader>
          <CardContent className="p-0">
            {historyLoading ? (
              <div className="p-6">
                <LoadingState message="Loading activity execution history..." />
              </div>
            ) : historyError ? (
              <div className="p-8 text-center space-y-3">
                <ShieldAlert className="w-8 h-8 text-rose-500 mx-auto" />
                <div className="text-sm font-semibold text-slate-900">
                  Activity Execution History Unavailable {historyErrorCode ? `(HTTP ${historyErrorCode})` : ""}
                </div>
                <p className="text-xs text-rose-600 max-w-md mx-auto">{historyError}</p>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleRetryHistory}
                  disabled={historyLoading}
                  className="gap-1.5"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${historyLoading ? "animate-spin" : ""}`} /> Retry History
                </Button>
              </div>
            ) : history.length === 0 ? (
              <div className="p-8 text-center text-slate-500 text-sm">
                Zero activity execution records captured for this instance yet.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Activity Name &amp; ID</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Assignee</TableHead>
                    <TableHead>Started</TableHead>
                    <TableHead>Ended</TableHead>
                    <TableHead className="text-right">Duration</TableHead>
                    <TableHead className="text-center">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {history.map((entry, idx) => (
                    <TableRow key={`${entry.activity_id}-${idx}`}>
                      <TableCell>
                        <div className="font-medium text-slate-900">
                          {entry.activity_name || entry.activity_id}
                        </div>
                        <div className="text-[11px] font-mono text-slate-400">
                          {entry.activity_id}
                        </div>
                      </TableCell>
                      <TableCell>
                        <span className="text-xs text-slate-600 bg-slate-100 px-2 py-0.5 rounded font-mono">
                          {humanizeActivityType(entry.activity_type)}
                        </span>
                      </TableCell>
                      <TableCell>
                        {entry.assignee ? (
                          <span className="text-xs font-medium text-slate-800 flex items-center gap-1">
                            <User className="w-3 h-3 text-slate-400" /> {entry.assignee}
                          </span>
                        ) : (
                          <span className="text-xs text-slate-400 italic">Automated</span>
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-slate-600 whitespace-nowrap">
                        {entry.start_time ? formatDateTime(entry.start_time) : "-"}
                      </TableCell>
                      <TableCell className="text-xs text-slate-600 whitespace-nowrap">
                        {entry.end_time ? formatDateTime(entry.end_time) : "-"}
                      </TableCell>
                      <TableCell className="text-xs text-slate-700 text-right font-mono tabular-nums">
                        {entry.duration_ms != null ? `${entry.duration_ms} ms` : "-"}
                      </TableCell>
                      <TableCell className="text-center">
                        {entry.completed ? (
                          <Badge variant="success" size="sm">
                            Completed
                          </Badge>
                        ) : (
                          <Badge variant="info" size="sm">
                            In Progress
                          </Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {/* Tab 2: Human Tasks */}
      {activeTab === "tasks" && (
        <Card className="border-slate-200 shadow-sm bg-white">
          <CardHeader className="pb-3 border-b border-slate-100 flex flex-row items-center justify-between">
            <CardTitle className="text-base font-semibold text-slate-900 flex items-center gap-2">
              <Inbox className="w-4 h-4 text-indigo-600" /> Human Tasks Bound to Instance
            </CardTitle>
            <Link to="/workflows/inbox">
              <Button variant="ghost" size="sm" className="text-indigo-600 text-xs">
                Open Full Inbox &rarr;
              </Button>
            </Link>
          </CardHeader>
          <CardContent className="p-0">
            {tasksLoading ? (
              <div className="p-6">
                <LoadingState message="Loading workflow human tasks..." />
              </div>
            ) : tasksError ? (
              <div className="p-8 text-center space-y-3">
                <ShieldAlert className="w-8 h-8 text-rose-500 mx-auto" />
                <div className="text-sm font-semibold text-slate-900">
                  Workflow Human Tasks Unavailable {tasksErrorCode ? `(HTTP ${tasksErrorCode})` : ""}
                </div>
                <p className="text-xs text-rose-600 max-w-md mx-auto">{tasksError}</p>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleRetryTasks}
                  disabled={tasksLoading}
                  className="gap-1.5"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${tasksLoading ? "animate-spin" : ""}`} /> Retry Tasks
                </Button>
              </div>
            ) : tasks.length === 0 ? (
              <div className="p-8 text-center text-slate-500 text-sm">
                Zero active human tasks assigned to this workflow instance.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Task Name</TableHead>
                    <TableHead>Definition Key</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Assignee / Role</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead>Due Date</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tasks.map((task) => (
                    <TableRow key={task.id}>
                      <TableCell className="font-semibold text-slate-900">
                        {task.name}
                      </TableCell>
                      <TableCell className="text-xs font-mono text-slate-500">
                        {task.task_definition_key}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            task.status === "COMPLETED"
                              ? "success"
                              : task.status === "CLAIMED"
                              ? "info"
                              : "warning"
                          }
                          size="sm"
                        >
                          {task.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-slate-700">
                        {task.assignee ? (
                          <span className="font-medium">{task.assignee}</span>
                        ) : task.candidate_roles && task.candidate_roles.length > 0 ? (
                          <span className="text-indigo-600">
                            Roles: {task.candidate_roles.join(", ")}
                          </span>
                        ) : (
                          <span className="text-slate-400 italic">Unassigned</span>
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-slate-500">
                        {formatDateTime(task.created_at)}
                      </TableCell>
                      <TableCell className="text-xs text-slate-500">
                        {task.due_at ? formatDateTime(task.due_at) : "-"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {/* Tab 3: Process Variables */}
      {activeTab === "variables" && (
        <Card className="border-slate-200 shadow-sm bg-white">
          <CardHeader className="pb-3 border-b border-slate-100">
            <CardTitle className="text-base font-semibold text-slate-900 flex items-center gap-2">
              <FileCode className="w-4 h-4 text-indigo-600" /> Process Variables (Display Redacted)
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4">
            {Object.keys(instance.input_variables || {}).length === 0 ? (
              <div className="p-8 text-center text-slate-500 text-sm">
                No process input variables provided for this instance execution.
              </div>
            ) : (
              <pre className="p-4 bg-slate-900 text-indigo-200 text-xs font-mono rounded-lg overflow-x-auto shadow-inner">
                {JSON.stringify(redactSensitiveForDisplay(instance.input_variables), null, 2)}
              </pre>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
};
