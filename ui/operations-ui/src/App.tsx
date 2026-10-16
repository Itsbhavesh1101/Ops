import React, { useState } from "react";
import { WorkflowApiClient } from "./api/client";
import { BpmnDesigner } from "./components/BpmnDesigner";
import { DmnEditor } from "./components/DmnEditor";
import { FormEditor } from "./components/FormEditor";
import { OperationsDashboard } from "./components/OperationsDashboard";
import { TaskInbox } from "./components/TaskInbox";

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<"dashboard" | "bpmn" | "dmn" | "forms" | "inbox">("dashboard");
  const [tenantId, setTenantId] = useState("tenant-engineering");

  // Reusable API client instance
  const apiClient = new WorkflowApiClient();

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* Top Header */}
      <header className="px-6 py-4 bg-slate-900 border-b border-slate-800 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded bg-blue-600 flex items-center justify-center font-bold text-white shadow-md shadow-blue-500/20">
            E7
          </div>
          <div>
            <h1 className="text-base font-bold tracking-tight text-white">
              IR Infotech E-7 Operations Engine
            </h1>
            <p className="text-xs text-slate-400">
              Workflow Modeler, Decision Runtime, Forms & Human Task Inbox
            </p>
          </div>
        </div>

        {/* Tenant Context Selector (Injected via Header, never manufactured internally) */}
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-400 font-medium">Tenant Context:</label>
          <select
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            className="px-3 py-1 bg-slate-800 border border-slate-700 rounded text-xs text-slate-200"
          >
            <option value="tenant-engineering">Faculty of Engineering</option>
            <option value="tenant-medicine">Faculty of Medicine</option>
            <option value="tenant-humanities">Faculty of Humanities</option>
          </select>
        </div>
      </header>

      {/* Navigation Sub-header */}
      <div className="px-6 py-3 bg-slate-900/60 border-b border-slate-800 flex items-center gap-2">
        <button
          onClick={() => setActiveTab("dashboard")}
          className={`tab-btn ${activeTab === "dashboard" ? "active" : ""}`}
        >
          Operations Dashboard
        </button>
        <button
          onClick={() => setActiveTab("bpmn")}
          className={`tab-btn ${activeTab === "bpmn" ? "active" : ""}`}
        >
          BPMN Designer
        </button>
        <button
          onClick={() => setActiveTab("dmn")}
          className={`tab-btn ${activeTab === "dmn" ? "active" : ""}`}
        >
          DMN Decision Modeler
        </button>
        <button
          onClick={() => setActiveTab("forms")}
          className={`tab-btn ${activeTab === "forms" ? "active" : ""}`}
        >
          Form Builder
        </button>
        <button
          onClick={() => setActiveTab("inbox")}
          className={`tab-btn ${activeTab === "inbox" ? "active" : ""}`}
        >
          Human Task Inbox
        </button>
      </div>

      {/* Main Content Area */}
      <main className="flex-1 p-6 max-w-7xl w-full mx-auto">
        {activeTab === "dashboard" && (
          <OperationsDashboard apiClient={apiClient} tenantId={tenantId} />
        )}
        {activeTab === "bpmn" && (
          <BpmnDesigner apiClient={apiClient} tenantId={tenantId} />
        )}
        {activeTab === "dmn" && (
          <DmnEditor apiClient={apiClient} tenantId={tenantId} />
        )}
        {activeTab === "forms" && <FormEditor />}
        {activeTab === "inbox" && (
          <TaskInbox apiClient={apiClient} tenantId={tenantId} />
        )}
      </main>

      {/* Footer & License Attribution Notice */}
      <footer className="px-6 py-3 bg-slate-900 border-t border-slate-800 text-xs text-slate-500 flex flex-wrap items-center justify-between">
        <span>IR Infotech E-7 Operations Engine — Phase 3B Workflow Subsystem</span>
        <span>bpmn.io Ecosystem (bpmn-js, dmn-js, form-js) Attribution Retained</span>
      </footer>
    </div>
  );
};

export default App;
