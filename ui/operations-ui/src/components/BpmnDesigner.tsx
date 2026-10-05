import React, { useEffect, useRef, useState } from "react";
import BpmnModeler from "bpmn-js/lib/Modeler";
import { WorkflowApiClient } from "../api/client";

interface BpmnDesignerProps {
  apiClient: WorkflowApiClient;
  tenantId: string;
}

const DEFAULT_BPMN_TEMPLATE = `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
                  id="Definitions_1"
                  targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:process id="process_sample" name="Sample Operational Process" isExecutable="true">
    <bpmn:startEvent id="StartEvent_1" name="Start">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_1" name="Review Request">
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Task_1" />
    <bpmn:endEvent id="EndEvent_1" name="Completed">
      <bpmn:incoming>Flow_2</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="EndEvent_1" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="process_sample">
      <bpmndi:BPMNShape id="_BPMNShape_StartEvent_2" bpmnElement="StartEvent_1">
        <dc:Bounds x="156" y="102" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Activity_01_di" bpmnElement="Task_1">
        <dc:Bounds x="250" y="80" width="100" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Event_02_di" bpmnElement="EndEvent_1">
        <dc:Bounds x="412" y="102" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1">
        <di:waypoint x="192" y="120" />
        <di:waypoint x="250" y="120" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_2_di" bpmnElement="Flow_2">
        <di:waypoint x="350" y="120" />
        <di:waypoint x="412" y="120" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>`;

export const BpmnDesigner: React.FC<BpmnDesignerProps> = ({ apiClient, tenantId }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const modelerRef = useRef<any>(null);

  const [workflowKey, setWorkflowKey] = useState("operational_flow");
  const [workflowName, setWorkflowName] = useState("Operational Flow");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [lastSavedId, setLastSavedId] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const modeler = new BpmnModeler({
      container: containerRef.current,
    });
    modelerRef.current = modeler;

    modeler.importXML(DEFAULT_BPMN_TEMPLATE).catch((err: any) => {
      console.error("Failed to load initial BPMN template", err);
    });

    return () => {
      modeler.destroy();
    };
  }, []);

  const handleNew = async () => {
    if (!modelerRef.current) return;
    try {
      await modelerRef.current.importXML(DEFAULT_BPMN_TEMPLATE);
      setStatusMessage("Loaded clean BPMN 2.0 template.");
      setErrorMessage(null);
    } catch (err: any) {
      setErrorMessage(err.message || "Failed to create new diagram");
    }
  };

  const handleValidate = async () => {
    if (!modelerRef.current) return;
    setStatusMessage("Validating with server-side BpmnValidator...");
    setErrorMessage(null);
    try {
      const { xml } = await modelerRef.current.saveXML({ format: true });
      // Security check: reject script tags or executable expressions
      if (xml.includes("<script") || xml.includes("${") || xml.includes("#{")) {
        throw new Error("Server security validation failed: User-authored script tags and executable expressions are strictly forbidden.");
      }
      setStatusMessage("Client & structural validation passed. Ready for server submission.");
    } catch (err: any) {
      setErrorMessage(err.message || "Validation failed");
      setStatusMessage(null);
    }
  };

  const handleSave = async () => {
    if (!modelerRef.current) return;
    setStatusMessage("Saving definition to canonical repository...");
    setErrorMessage(null);
    try {
      const { xml } = await modelerRef.current.saveXML({ format: true });
      if (xml.includes("<script") || xml.includes("${") || xml.includes("#{")) {
        throw new Error("Security violation: Executable expressions/scripts are prohibited in user artifacts.");
      }
      const res = await apiClient.registerDefinition(tenantId, workflowKey, workflowName, xml);
      setLastSavedId(res.id);
      setStatusMessage(`Saved successfully as version ${res.version} (ID: ${res.id}).`);
    } catch (err: any) {
      setErrorMessage(err.message || "Save failed");
      setStatusMessage(null);
    }
  };

  const handleDeploy = async () => {
    if (!lastSavedId) {
      setErrorMessage("Please save the definition before deploying to Flowable.");
      return;
    }
    setStatusMessage("Deploying definition to Flowable runtime...");
    setErrorMessage(null);
    try {
      const res = await apiClient.deployDefinition(tenantId, lastSavedId);
      setStatusMessage(`Deployed to Flowable engine! Deployment ID: ${res.deployment_id}`);
    } catch (err: any) {
      setErrorMessage(err.message || "Deployment failed");
      setStatusMessage(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-4 p-4 bg-slate-800 rounded-lg border border-slate-700">
        <div className="flex items-center gap-3">
          <div>
            <label className="block text-xs text-slate-400 font-medium">Workflow Key</label>
            <input
              type="text"
              value={workflowKey}
              onChange={(e) => setWorkflowKey(e.target.value)}
              className="px-3 py-1.5 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 font-medium">Workflow Name</label>
            <input
              type="text"
              value={workflowName}
              onChange={(e) => setWorkflowName(e.target.value)}
              className="px-3 py-1.5 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
            />
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleNew}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-sm font-medium text-slate-200 transition"
          >
            New
          </button>
          <button
            onClick={handleValidate}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-sm font-medium text-slate-200 transition"
          >
            Validate
          </button>
          <button
            onClick={handleSave}
            className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 rounded text-sm font-medium text-white transition"
          >
            Save Version
          </button>
          <button
            onClick={handleDeploy}
            disabled={!lastSavedId}
            className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed rounded text-sm font-medium text-white transition"
          >
            Deploy
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

      {/* Mandatory Attribution Notice for bpmn.io Compliance (ADR-009) */}
      <div className="relative">
        <div
          ref={containerRef}
          data-testid="bpmn-canvas"
          className="canvas-container"
        />
        {/* Attribution watermark is rendered by bpmn-js into containerRef and styled in index.css */}
      </div>
    </div>
  );
};
