import React, { useEffect, useRef, useState } from "react";
// @ts-expect-error dmn-js untyped import
import DmnModeler from "dmn-js/lib/Modeler";
import { WorkflowApiClient } from "../api/client";

interface DmnEditorProps {
  apiClient: WorkflowApiClient;
  tenantId: string;
}

const DEFAULT_DMN_TEMPLATE = `<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/"
             xmlns:dmndi="https://www.omg.org/spec/DMN/20191111/DMNDI/"
             xmlns:dc="http://www.omg.org/spec/DMN/20180521/DC/"
             id="Definitions_Routing"
             name="Approval Routing Decision"
             namespace="http://irinfotech.com/dmn">
  <decision id="decision_approval_route" name="Approval Routing">
    <decisionTable id="DecisionTable_1" hitPolicy="FIRST">
      <input id="Input_1" label="Risk Band">
        <inputExpression id="InputExpression_1" typeRef="string">
          <text>risk_band</text>
        </inputExpression>
      </input>
      <input id="Input_2" label="Budget Amount">
        <inputExpression id="InputExpression_2" typeRef="number">
          <text>budget_amount</text>
        </inputExpression>
      </input>
      <output id="Output_1" label="Approval Level" name="approval_level" typeRef="string" />
      <rule id="Rule_1">
        <inputEntry id="InputEntry_1">
          <text>"HIGH"</text>
        </inputEntry>
        <inputEntry id="InputEntry_2">
          <text>&gt; 100000</text>
        </inputEntry>
        <outputEntry id="OutputEntry_1">
          <text>"VP_APPROVAL"</text>
        </outputEntry>
      </rule>
      <rule id="Rule_2">
        <inputEntry id="InputEntry_3">
          <text>"MEDIUM"</text>
        </inputEntry>
        <inputEntry id="InputEntry_4">
          <text>&gt; 25000</text>
        </inputEntry>
        <outputEntry id="OutputEntry_2">
          <text>"DIRECTOR_APPROVAL"</text>
        </outputEntry>
      </rule>
      <rule id="Rule_3">
        <inputEntry id="InputEntry_5">
          <text>-</text>
        </inputEntry>
        <inputEntry id="InputEntry_6">
          <text>-</text>
        </inputEntry>
        <outputEntry id="OutputEntry_3">
          <text>"MANAGER_APPROVAL"</text>
        </outputEntry>
      </rule>
    </decisionTable>
  </decision>
  <dmndi:DMNDI>
    <dmndi:DMNDiagram id="DMNDiagram_1">
      <dmndi:DMNShape id="DMNShape_1" dmnElementRef="decision_approval_route">
        <dc:Bounds height="80" width="180" x="160" y="100" />
      </dmndi:DMNShape>
    </dmndi:DMNDiagram>
  </dmndi:DMNDI>
</definitions>`;

export const DmnEditor: React.FC<DmnEditorProps> = ({ apiClient, tenantId }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const modelerRef = useRef<any>(null);

  const [decisionKey, setDecisionKey] = useState("decision_approval_route");
  const [resourceName, setResourceName] = useState("approval_routing_v1");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Test Execution State
  const [testRiskBand, setTestRiskBand] = useState("HIGH");
  const [testBudget, setTestBudget] = useState(150000);
  const [evalResult, setEvalResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const modeler = new DmnModeler({
      container: containerRef.current,
    });
    modelerRef.current = modeler;

    modeler.importXML(DEFAULT_DMN_TEMPLATE).catch((err: any) => {
      console.error("Failed to load initial DMN template", err);
    });

    return () => {
      modeler.destroy();
    };
  }, []);

  const handleValidate = async () => {
    if (!modelerRef.current) return;
    setStatusMessage("Validating DMN decision table...");
    setErrorMessage(null);
    try {
      const { xml } = await modelerRef.current.saveXML({ format: true });
      if (xml.includes("<!DOCTYPE") || xml.includes("<!ENTITY") || xml.includes("<script")) {
        throw new Error("Security violation: DTD and scripts are strictly prohibited (XXE prevention).");
      }
      setStatusMessage("DMN security and XML syntax valid.");
    } catch (err: any) {
      setErrorMessage(err.message || "DMN Validation failed");
      setStatusMessage(null);
    }
  };

  const handleDeploy = async () => {
    if (!modelerRef.current) return;
    setStatusMessage("Deploying DMN decision table to Flowable runtime...");
    setErrorMessage(null);
    try {
      const { xml } = await modelerRef.current.saveXML({ format: true });
      const res = await apiClient.deployDecision(tenantId, xml, resourceName);
      setStatusMessage(`Deployed decision '${res.decision_key}'! Rules: ${res.rule_count}, Hit Policy: ${res.hit_policy}`);
    } catch (err: any) {
      setErrorMessage(err.message || "Deployment failed");
      setStatusMessage(null);
    }
  };

  const handleExecuteTest = async () => {
    setStatusMessage("Evaluating decision table deterministically...");
    setErrorMessage(null);
    setEvalResult(null);
    try {
      const res = await apiClient.executeDecision(tenantId, decisionKey, {
        risk_band: testRiskBand,
        budget_amount: Number(testBudget),
      });
      setEvalResult(res.result_variables);
      setStatusMessage(`Decision evaluated at ${res.evaluated_at}.`);
    } catch (err: any) {
      setErrorMessage(err.message || "Decision execution failed");
      setStatusMessage(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-4 p-4 bg-slate-800 rounded-lg border border-slate-700">
        <div className="flex items-center gap-3">
          <div>
            <label className="block text-xs text-slate-400 font-medium">Decision Key</label>
            <input
              type="text"
              value={decisionKey}
              onChange={(e) => setDecisionKey(e.target.value)}
              className="px-3 py-1.5 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 font-medium">Resource Name</label>
            <input
              type="text"
              value={resourceName}
              onChange={(e) => setResourceName(e.target.value)}
              className="px-3 py-1.5 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
            />
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleValidate}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-sm font-medium text-slate-200 transition"
          >
            Validate
          </button>
          <button
            onClick={handleDeploy}
            className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded text-sm font-medium text-white transition"
          >
            Deploy to Engine
          </button>
        </div>
      </div>

      {statusMessage && (
        <div className="p-3 bg-blue-950/60 border border-blue-800 rounded text-sm text-blue-200">
          {statusMessage}
        </div>
      )}

      {errorMessage && (
        <div className="p-3 bg-rose-950/60 border border-rose-800 rounded text-sm text-rose-200">
          {errorMessage}
        </div>
      )}

      <div className="relative">
        <div
          ref={containerRef}
          data-testid="dmn-canvas"
          className="canvas-container"
        />
      </div>

      {/* Deterministic Evaluation Testing Panel */}
      <div className="p-4 bg-slate-800/80 border border-slate-700 rounded-lg flex flex-col gap-3">
        <h4 className="text-sm font-semibold text-slate-200">Test Deterministic Rule Evaluation</h4>
        <div className="flex flex-wrap items-center gap-4">
          <div>
            <label className="block text-xs text-slate-400 font-medium">Input: risk_band</label>
            <select
              value={testRiskBand}
              onChange={(e) => setTestRiskBand(e.target.value)}
              className="px-3 py-1.5 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
            >
              <option value="LOW">LOW</option>
              <option value="MEDIUM">MEDIUM</option>
              <option value="HIGH">HIGH</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-400 font-medium">Input: budget_amount</label>
            <input
              type="number"
              value={testBudget}
              onChange={(e) => setTestBudget(Number(e.target.value))}
              className="px-3 py-1.5 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
            />
          </div>
          <button
            onClick={handleExecuteTest}
            className="mt-4 px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded text-sm font-medium text-white transition"
          >
            Evaluate Rule
          </button>
        </div>

        {evalResult && (
          <div className="mt-2 p-3 bg-slate-900 rounded border border-slate-700">
            <span className="text-xs text-slate-400 block mb-1">Deterministic Output:</span>
            <pre className="text-xs text-emerald-400 font-mono">
              {JSON.stringify(evalResult, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
};
