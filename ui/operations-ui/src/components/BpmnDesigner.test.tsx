import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BpmnDesigner } from "./BpmnDesigner";
import { WorkflowApiClient } from "../api/client";

const mockSaveXmlFn = vi.fn().mockResolvedValue({
  xml: `<?xml version="1.0" encoding="UTF-8"?><bpmn:definitions id="Definitions_1"><bpmn:process id="p1" isExecutable="true"/></bpmn:definitions>`,
});

vi.mock("bpmn-js/lib/Modeler", () => {
  return {
    default: class MockBpmnModeler {
      container: HTMLElement;
      constructor(options: { container: HTMLElement }) {
        this.container = options.container;
        // Inject bpmn.io watermark element into container (Mandatory Attribution Invariant)
        const watermark = document.createElement("div");
        watermark.className = "bjs-powered-by";
        watermark.setAttribute("data-testid", "bpmn-watermark");
        watermark.textContent = "bpmn.io";
        this.container.appendChild(watermark);
      }
      importXML = vi.fn().mockResolvedValue({ warnings: [] });
      saveXML = mockSaveXmlFn;
      destroy = vi.fn();
    },
  };
});

describe("BpmnDesigner Component", () => {
  let mockApiClient: WorkflowApiClient;

  beforeEach(() => {
    mockApiClient = new WorkflowApiClient();
    mockApiClient.registerDefinition = vi.fn().mockResolvedValue({
      id: "def-12345",
      tenant_id: "tenant-engineering",
      workflow_key: "operational_flow",
      name: "Operational Flow",
      version: 1,
      bpmn_process_id: "process_sample",
      artifact_hash: "hash_abc123",
      deployment_id: null,
      is_active: true,
    });
    mockApiClient.deployDefinition = vi.fn().mockResolvedValue({
      id: "def-12345",
      tenant_id: "tenant-engineering",
      workflow_key: "operational_flow",
      name: "Operational Flow",
      version: 1,
      bpmn_process_id: "process_sample",
      artifact_hash: "hash_abc123",
      deployment_id: "flowable-dep-999",
      is_active: true,
    });
  });

  it("renders designer canvas and preserves mandatory bpmn.io watermark attribution", () => {
    render(<BpmnDesigner apiClient={mockApiClient} tenantId="tenant-engineering" />);
    const canvas = screen.getByTestId("bpmn-canvas");
    expect(canvas).toBeInTheDocument();

    // Verify mandatory watermark is present in DOM
    const watermark = screen.getByTestId("bpmn-watermark");
    expect(watermark).toBeInTheDocument();
    expect(watermark).toHaveClass("bjs-powered-by");
  });

  it("validates BPMN and saves valid definition via client", async () => {
    render(<BpmnDesigner apiClient={mockApiClient} tenantId="tenant-engineering" />);

    const saveButton = screen.getByRole("button", { name: /save version/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(mockApiClient.registerDefinition).toHaveBeenCalledWith(
        "tenant-engineering",
        "operational_flow",
        "Operational Flow",
        expect.stringContaining("<bpmn:definitions")
      );
    });

    expect(
      screen.getByText(/Saved successfully as version 1 \(ID: def-12345\)/i)
    ).toBeInTheDocument();
  });

  it("surfaces server validation errors cleanly when artifact violates rules", async () => {
    // Mock saveXML returning forbidden script injection
    mockSaveXmlFn.mockResolvedValueOnce({
      xml: `<bpmn:definitions><script>alert('xss')</script></bpmn:definitions>`,
    });

    render(<BpmnDesigner apiClient={mockApiClient} tenantId="tenant-engineering" />);

    const validateButton = screen.getByRole("button", { name: /validate/i });
    fireEvent.click(validateButton);

    await waitFor(() => {
      expect(
        screen.getByText(/User-authored script tags and executable expressions are strictly forbidden/i)
      ).toBeInTheDocument();
    });
  });
});
