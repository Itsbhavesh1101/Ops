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
import { ForbiddenPage } from "./pages/ForbiddenPage";
import { NotFoundPage } from "./pages/NotFoundPage";

// Phase F2 Feature Screen Pages
import { GoalsPage } from "./pages/GoalsPage";
import { GoalDetailPage } from "./pages/GoalDetailPage";
import { PlansPage } from "./pages/PlansPage";
import { PlanDetailPage } from "./pages/PlanDetailPage";
import { TasksPage } from "./pages/TasksPage";
import { ResourcesPage } from "./pages/ResourcesPage";
import { AvailabilityPage } from "./pages/AvailabilityPage";
import { SchedulingJobsPage } from "./pages/SchedulingJobsPage";
import { SchedulesPage } from "./pages/SchedulesPage";
import { ScheduleDetailPage } from "./pages/ScheduleDetailPage";

// Phase F3 Real Implementations
import { WorkflowInstancePage } from "./pages/WorkflowInstancePage";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { AuditPage } from "./pages/AuditPage";

// Workflow Subsystem Existing Components
import { TaskInbox } from "./components/TaskInbox";
import { BpmnDesigner } from "./components/BpmnDesigner";
import { DmnEditor } from "./components/DmnEditor";
import { FormEditor } from "./components/FormEditor";

import { ErrorState } from "./components/ui/ErrorState";

const WorkflowInboxView: React.FC = () => {
  const { user } = useAuth();
  if (!user?.tenant_id) {
    return (
      <ErrorState
        title="Session or Tenant Context Missing"
        message="Verified tenant identity is required to access workflow task resources. Please sign in with an authorized tenant account."
      />
    );
  }
  return <TaskInbox apiClient={apiClient} tenantId={user.tenant_id} />;
};

const WorkflowBpmnView: React.FC = () => {
  const { user } = useAuth();
  if (!user?.tenant_id) {
    return (
      <ErrorState
        title="Session or Tenant Context Missing"
        message="Verified tenant identity is required to access workflow model resources. Please sign in with an authorized tenant account."
      />
    );
  }
  return <BpmnDesigner apiClient={apiClient} tenantId={user.tenant_id} />;
};

const WorkflowDmnView: React.FC = () => {
  const { user } = useAuth();
  if (!user?.tenant_id) {
    return (
      <ErrorState
        title="Session or Tenant Context Missing"
        message="Verified tenant identity is required to access decision model resources. Please sign in with an authorized tenant account."
      />
    );
  }
  return <DmnEditor apiClient={apiClient} tenantId={user.tenant_id} />;
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

                {/* Planning & Intelligence Module (Phase F2) */}
                <Route path="/goals" element={<GoalsPage />} />
                <Route path="/goals/:goalId" element={<GoalDetailPage />} />
                <Route path="/plans" element={<PlansPage />} />
                <Route path="/plans/:planId" element={<PlanDetailPage />} />
                <Route path="/tasks" element={<TasksPage />} />

                {/* Workflows & Automation Subsystem */}
                <Route path="/workflows" element={<Navigate to="/workflows/inbox" replace />} />
                <Route path="/workflows/inbox" element={<WorkflowInboxView />} />
                <Route path="/workflows/bpmn" element={<WorkflowBpmnView />} />
                <Route path="/workflows/dmn" element={<WorkflowDmnView />} />
                <Route path="/workflows/forms" element={<WorkflowFormsView />} />
                <Route path="/workflows/:instanceId" element={<WorkflowInstancePage />} />

                {/* Resources Subsystem (Phase F2) */}
                <Route path="/resources" element={<ResourcesPage />} />
                <Route path="/resources/availability" element={<AvailabilityPage />} />

                {/* Scheduling Engine (Phase F2) */}
                <Route path="/scheduling/jobs" element={<SchedulingJobsPage />} />
                <Route path="/schedules" element={<SchedulesPage />} />
                <Route path="/schedules/:scheduleId" element={<ScheduleDetailPage />} />

                {/* Operations & Governance */}
                <Route path="/analytics" element={<AnalyticsPage />} />
                <Route path="/audit" element={<AuditPage />} />

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
