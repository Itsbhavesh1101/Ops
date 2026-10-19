import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ToastProvider } from "./components/ui/Toast";
import { AppShell } from "./components/layout/AppShell";
import { ProtectedRoute } from "./components/layout/ProtectedRoute";
import { apiClient } from "./api/client";

// Pages
import { LoginPage } from "./pages/LoginPage";
import { DashboardPage } from "./pages/DashboardPage";
import { SystemPage } from "./pages/SystemPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { ForbiddenPage } from "./pages/ForbiddenPage";
import { NotFoundPage } from "./pages/NotFoundPage";

// Workflow Subsystem Existing Components
import { TaskInbox } from "./components/TaskInbox";
import { BpmnDesigner } from "./components/BpmnDesigner";
import { DmnEditor } from "./components/DmnEditor";
import { FormEditor } from "./components/FormEditor";

const WorkflowInboxView: React.FC = () => {
  const { user } = useAuth();
  const tenantId = user?.tenant_id || "tenant-engineering";
  return <TaskInbox apiClient={apiClient} tenantId={tenantId} />;
};

const WorkflowBpmnView: React.FC = () => {
  const { user } = useAuth();
  const tenantId = user?.tenant_id || "tenant-engineering";
  return <BpmnDesigner apiClient={apiClient} tenantId={tenantId} />;
};

const WorkflowDmnView: React.FC = () => {
  const { user } = useAuth();
  const tenantId = user?.tenant_id || "tenant-engineering";
  return <DmnEditor apiClient={apiClient} tenantId={tenantId} />;
};

const WorkflowFormsView: React.FC = () => {
  return <FormEditor />;
};

export const AppRoutes: React.FC = () => {
  return (
    <Routes>
      {/* Public Authentication Route */}
      <Route path="/login" element={<LoginPage />} />

      {/* Authenticated Application Shell */}
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <AppShell>
              <Routes>
                <Route path="/" element={<Navigate to="/dashboard" replace />} />
                <Route path="/dashboard" element={<DashboardPage />} />

                {/* Planning & Intelligence Module */}
                <Route
                  path="/goals"
                  element={
                    <PlaceholderPage
                      title="Operational Goals"
                      moduleName="Planning & Intelligence"
                      targetPhase="Phase F2"
                      description="Operational goal definition, hierarchy decomposition, and target KPIs."
                      backendEndpoint="POST /api/v1/planning/goals, GET /api/v1/planning/goals"
                    />
                  }
                />
                <Route
                  path="/goals/:goalId"
                  element={
                    <PlaceholderPage
                      title="Goal Breakdown & Hierarchy"
                      moduleName="Planning & Intelligence"
                      targetPhase="Phase F2"
                      description="Detailed sub-goal hierarchy, constraints, and target metric allocations."
                      backendEndpoint="GET /api/v1/planning/goals/:goalId"
                    />
                  }
                />
                <Route
                  path="/plans"
                  element={
                    <PlaceholderPage
                      title="Operational Plans"
                      moduleName="Planning & Intelligence"
                      targetPhase="Phase F2"
                      description="Automated plan synthesis, step sequences, and WBS breakdown."
                      backendEndpoint="POST /api/v1/planning/plans/generate, GET /api/v1/planning/plans"
                    />
                  }
                />
                <Route
                  path="/plans/:planId"
                  element={
                    <PlaceholderPage
                      title="Plan Inspection & Gantt View"
                      moduleName="Planning & Intelligence"
                      targetPhase="Phase F2"
                      description="Step sequencing, milestone tracking, and dependency graphs."
                      backendEndpoint="GET /api/v1/planning/plans/:planId"
                    />
                  }
                />
                <Route
                  path="/tasks"
                  element={
                    <PlaceholderPage
                      title="Work Breakdown Structure (WBS)"
                      moduleName="Planning & Intelligence"
                      targetPhase="Phase F2"
                      description="Hierarchical task breakdown, predecessor constraints, and milestone tracking."
                      backendEndpoint="GET /api/v1/planning/plans/:planId/tasks"
                    />
                  }
                />

                {/* Workflows & Automation Subsystem */}
                <Route path="/workflows" element={<Navigate to="/workflows/inbox" replace />} />
                <Route path="/workflows/inbox" element={<WorkflowInboxView />} />
                <Route path="/workflows/bpmn" element={<WorkflowBpmnView />} />
                <Route path="/workflows/dmn" element={<WorkflowDmnView />} />
                <Route path="/workflows/forms" element={<WorkflowFormsView />} />
                <Route
                  path="/workflows/:instanceId"
                  element={
                    <PlaceholderPage
                      title="Workflow Instance Execution"
                      moduleName="Workflows & Automation"
                      targetPhase="Phase F3"
                      description="Real-time process execution tracing, variable inspection, and flow token visualization."
                      backendEndpoint="GET /api/v1/workflows/instances/:id"
                    />
                  }
                />

                {/* Resources Subsystem */}
                <Route
                  path="/resources"
                  element={
                    <PlaceholderPage
                      title="Resource Catalog"
                      moduleName="Resources"
                      targetPhase="Phase F2"
                      description="Enterprise workforce, equipment, facilities, and capacity inventory."
                      backendEndpoint="GET /api/v1/resources/catalog"
                    />
                  }
                />
                <Route
                  path="/resources/availability"
                  element={
                    <PlaceholderPage
                      title="Resource Availability Windows"
                      moduleName="Resources"
                      targetPhase="Phase F2"
                      description="Shift calendars, blackout windows, maintenance intervals, and capacity quotas."
                      backendEndpoint="GET /api/v1/resources/availability"
                    />
                  }
                />

                {/* Scheduling Engine */}
                <Route
                  path="/scheduling/jobs"
                  element={
                    <PlaceholderPage
                      title="Scheduling Engine Jobs"
                      moduleName="Scheduling Engine"
                      targetPhase="Phase F2"
                      description="Asynchronous solver orchestration, constraint satisfaction jobs, and solver logs."
                      backendEndpoint="POST /api/v1/scheduling/jobs, GET /api/v1/scheduling/jobs"
                    />
                  }
                />
                <Route
                  path="/schedules"
                  element={
                    <PlaceholderPage
                      title="Timetables & Schedules"
                      moduleName="Scheduling Engine"
                      targetPhase="Phase F2"
                      description="Feasible timetable schedules, assignment matrix, and conflict inspection."
                      backendEndpoint="GET /api/v1/schedules"
                    />
                  }
                />
                <Route
                  path="/schedules/:scheduleId"
                  element={
                    <PlaceholderPage
                      title="Schedule Details & Assignment Matrix"
                      moduleName="Scheduling Engine"
                      targetPhase="Phase F2"
                      description="Detailed resource-slot assignment matrix, conflict diagnostics, and feasibility proof."
                      backendEndpoint="GET /api/v1/schedules/:scheduleId"
                    />
                  }
                />

                {/* Operations & Governance */}
                <Route
                  path="/analytics"
                  element={
                    <PlaceholderPage
                      title="Operations Analytics"
                      moduleName="Operations & Governance"
                      targetPhase="Phase F3"
                      description="Long-term operational throughput, SLA heatmaps, and cross-tenant capacity telemetry."
                      backendEndpoint="GET /api/v1/analytics/operations"
                    />
                  }
                />
                <Route
                  path="/audit"
                  element={
                    <PlaceholderPage
                      title="Enterprise Audit Trail"
                      moduleName="Operations & Governance"
                      targetPhase="Phase F3"
                      description="Immutable security logs, state change history, and compliance attestation."
                      backendEndpoint="GET /api/v1/audit/trail"
                    />
                  }
                />

                {/* System Diagnostics */}
                <Route path="/system" element={<SystemPage />} />

                {/* Standard Fallbacks */}
                <Route path="/forbidden" element={<ForbiddenPage />} />
                <Route path="*" element={<NotFoundPage />} />
              </Routes>
            </AppShell>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
};

export const App: React.FC = () => {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <AppRoutes />
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
};

export default App;
