import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DmnEditor } from "./DmnEditor";
import { WorkflowApiClient } from "../api/client";

// Mock dmn-js Modeler for jsdom environment
vi.mock("dmn-js/lib/Modeler", () => {
  return {
    default: class MockDmnModeler {
      container: HTMLElement;
      constructor(options: { container: HTMLElement }) {
        this.container = options.container;
        const watermark = document.createElement("div");
        watermark.className = "dmn-powered-by";
        watermark.setAttribute("data-testid", "dmn-watermark");
        watermark.textContent = "bpmn.io";
        this.container.appendChild(watermark);
      }
      importXML = vi.fn().mockResolvedValue({ warnings: [] });
      saveXML = vi.fn().mockResolvedValue({
        xml: `<?xml version="1.0" encoding="UTF-8"?><definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/"><decision id="d1"/></definitions>`,
      });
      destroy = vi.fn();
    },
  };
});

describe("DmnEditor Component", () => {
  let mockApiClient: WorkflowApiClient;

  beforeEach(() => {
    mockApiClient = new WorkflowApiClient();
    mockApiClient.deployDecision = vi.fn().mockResolvedValue({
      decision_key: "decision_approval_route",
      name: "Approval Routing",
      hit_policy: "FIRST",
      rule_count: 3,
      artifact_hash: "dmn_hash_123",
    });
    mockApiClient.executeDecision = vi.fn().mockResolvedValue({
      decision_key: "decision_approval_route",
      result_variables: { approval_level: "VP_APPROVAL" },
      evaluated_at: "2026-10-05T01:00:00Z",
    });
  });

  it("renders DMN editor canvas and preserves mandatory bpmn.io attribution watermark", () => {
    render(<DmnEditor apiClient={mockApiClient} tenantId="tenant-engineering" />);
    const canvas = screen.getByTestId("dmn-canvas");
    expect(canvas).toBeInTheDocument();

    const watermark = screen.getByTestId("dmn-watermark");
    expect(watermark).toBeInTheDocument();
    expect(watermark).toHaveClass("dmn-powered-by");
  });

  it("deploys DMN decision table to engine via client", async () => {
    render(<DmnEditor apiClient={mockApiClient} tenantId="tenant-engineering" />);

    const deployButton = screen.getByRole("button", { name: /deploy to engine/i });
    fireEvent.click(deployButton);

    await waitFor(() => {
      expect(mockApiClient.deployDecision).toHaveBeenCalledWith(
        "tenant-engineering",
        expect.stringContaining("<definitions"),
        "approval_routing_v1"
      );
    });

    expect(
      screen.getByText(/Deployed decision 'decision_approval_route'! Rules: 3, Hit Policy: FIRST/i)
    ).toBeInTheDocument();
  });

  it("executes deterministic rule evaluation test and surfaces output", async () => {
    render(<DmnEditor apiClient={mockApiClient} tenantId="tenant-engineering" />);

    const evalButton = screen.getByRole("button", { name: /evaluate rule/i });
    fireEvent.click(evalButton);

    await waitFor(() => {
      expect(mockApiClient.executeDecision).toHaveBeenCalledWith(
        "tenant-engineering",
        "decision_approval_route",
        { risk_band: "HIGH", budget_amount: 150000 }
      );
    });

    expect(screen.getByText(/VP_APPROVAL/i)).toBeInTheDocument();
  });
});
