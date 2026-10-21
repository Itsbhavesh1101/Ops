import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TaskInbox } from "./TaskInbox";
import { WorkflowApiClient, WorkflowTask } from "../api/client";

const TEST_INBOX_TASKS: WorkflowTask[] = [
  {
    id: "7b8d4f20-91a3-4819-a982-1d54ef98b101",
    tenant_id: "tenant-engineering",
    task_definition_key: "task_chair_approval",
    name: "Department Chair Review",
    description: "Review academic semester course scheduling draft",
    status: "PENDING",
    assignee: null,
    candidate_roles: ["department_chair", "academic_lead"],
    form_key: "form_exam_review",
    sla_status: "WARNING",
    warning_at: "2026-10-05T04:00:00Z",
    due_at: "2026-10-05T08:00:00Z",
    created_at: "2026-10-04T22:00:00Z",
  },
  {
    id: "9c3e2180-2a54-4f81-b271-9e812d4a5102",
    tenant_id: "tenant-engineering",
    task_definition_key: "task_dean_review",
    name: "Dean Final Sign-off",
    description: "Approve semester exam timetable and hall allocations",
    status: "CLAIMED",
    assignee: "dean_morris",
    candidate_roles: ["academic_dean"],
    form_key: "form_dean_signoff",
    sla_status: "RUNNING",
    warning_at: "2026-10-05T12:00:00Z",
    due_at: "2026-10-05T18:00:00Z",
    created_at: "2026-10-05T00:30:00Z",
  },
];

describe("TaskInbox Component", () => {
  let mockApiClient: WorkflowApiClient;

  beforeEach(() => {
    mockApiClient = new WorkflowApiClient();
    mockApiClient.listTasks = vi.fn().mockResolvedValue(TEST_INBOX_TASKS);
    mockApiClient.performTaskAction = vi.fn().mockResolvedValue({
      status: "ACCEPTED",
      task_id: "7b8d4f20-91a3-4819-a982-1d54ef98b101",
    });
  });

  it("renders task inbox with canonical tasks and SLA warning badge", async () => {
    render(<TaskInbox apiClient={mockApiClient} tenantId="tenant-engineering" initialTasks={TEST_INBOX_TASKS} />);

    expect(screen.getByText("Department Chair Review")).toBeInTheDocument();
    expect(screen.getByText("Dean Final Sign-off")).toBeInTheDocument();
    expect(screen.getByText("SLA Warning")).toBeInTheDocument();
  });

  it("submits approval action with client-generated Idempotency-Key and tenant context", async () => {
    render(<TaskInbox apiClient={mockApiClient} tenantId="tenant-engineering" initialTasks={TEST_INBOX_TASKS} />);

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

    render(<TaskInbox apiClient={mockApiClient} tenantId="tenant-engineering" initialTasks={TEST_INBOX_TASKS} />);

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
