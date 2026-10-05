import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TaskInbox } from "./TaskInbox";
import { WorkflowApiClient } from "../api/client";

describe("TaskInbox Component", () => {
  let mockApiClient: WorkflowApiClient;

  beforeEach(() => {
    mockApiClient = new WorkflowApiClient();
    mockApiClient.performTaskAction = vi.fn().mockResolvedValue({
      status: "ACCEPTED",
      task_id: "7b8d4f20-91a3-4819-a982-1d54ef98b101",
    });
  });

  it("renders task inbox with canonical tasks and SLA warning badge", () => {
    render(<TaskInbox apiClient={mockApiClient} tenantId="tenant-engineering" />);

    expect(screen.getByText("Department Chair Review")).toBeInTheDocument();
    expect(screen.getByText("Dean Final Sign-off")).toBeInTheDocument();
    expect(screen.getByText("SLA Warning")).toBeInTheDocument();
  });

  it("submits approval action with client-generated Idempotency-Key and tenant context", async () => {
    render(<TaskInbox apiClient={mockApiClient} tenantId="tenant-engineering" />);

    // Select the first task
    const taskCard = screen.getByText("Department Chair Review");
    fireEvent.click(taskCard);

    // Enter review comment
    const commentInput = screen.getByPlaceholderText(/enter decision rationale/i);
    fireEvent.change(commentInput, { target: { value: "Approved following curriculum review" } });

    // Click submit approval
    const approveButton = screen.getByRole("button", { name: /submit approval/i });
    fireEvent.click(approveButton);

    await waitFor(() => {
      expect(mockApiClient.performTaskAction).toHaveBeenCalledWith(
        "tenant-engineering",
        "7b8d4f20-91a3-4819-a982-1d54ef98b101",
        "approve",
        expect.objectContaining({
          comment: "Approved following curriculum review",
          decision: "APPROVE",
        }),
        expect.stringMatching(/^idemp_/)
      );
    });

    expect(screen.getByText(/Action 'approve' submitted successfully!/i)).toBeInTheDocument();
  });

  it("surfaces server errors cleanly on action failure without leaking internal engine state", async () => {
    mockApiClient.performTaskAction = vi.fn().mockRejectedValueOnce(
      new Error("Principal is not authorized to approve task '7b8d4f20'")
    );

    render(<TaskInbox apiClient={mockApiClient} tenantId="tenant-engineering" />);

    const taskCard = screen.getByText("Department Chair Review");
    fireEvent.click(taskCard);

    const approveButton = screen.getByRole("button", { name: /submit approval/i });
    fireEvent.click(approveButton);

    await waitFor(() => {
      expect(
        screen.getByText(/Principal is not authorized to approve task '7b8d4f20'/i)
      ).toBeInTheDocument();
    });
  });
});
