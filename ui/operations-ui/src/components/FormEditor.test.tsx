import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FormEditor } from "./FormEditor";

describe("FormEditor Component", () => {
  it("renders form builder with default components", () => {
    render(<FormEditor />);
    expect(screen.getByText(/Semester Exam Review Form/i)).toBeInTheDocument();
    expect(screen.getByText(/Review Comments/i)).toBeInTheDocument();
    expect(screen.getByText(/Quality Rating/i)).toBeInTheDocument();
  });

  it("adds a new typed field component to the form schema", () => {
    render(<FormEditor />);

    const keyInput = screen.getByPlaceholderText(/dean_remarks/i);
    const labelInput = screen.getByPlaceholderText(/Dean Remarks/i);
    const addButton = screen.getByRole("button", { name: /add field/i });

    fireEvent.change(keyInput, { target: { value: "dean_notes" } });
    fireEvent.change(labelInput, { target: { value: "Dean Final Notes" } });
    fireEvent.click(addButton);

    expect(screen.getByText("Dean Final Notes")).toBeInTheDocument();
    expect(screen.getByText("(dean_notes)")).toBeInTheDocument();
  });

  it("derives explicit variable allowlist policy from schema components", () => {
    render(<FormEditor />);

    const policyTab = screen.getByRole("button", { name: /derived variable policy/i });
    fireEvent.click(policyTab);

    expect(screen.getByText("approval_comment")).toBeInTheDocument();
    expect(screen.getByText("quality_score")).toBeInTheDocument();
    expect(screen.getByText("decision_outcome")).toBeInTheDocument();
  });

  it("validates mandatory fields in preview mode", () => {
    render(<FormEditor />);

    const previewTab = screen.getByRole("button", { name: /rendered preview/i });
    fireEvent.click(previewTab);

    const submitButton = screen.getByRole("button", { name: /test submission validation/i });
    fireEvent.click(submitButton);

    expect(
      screen.getByText(/Validation error: 'Review Comments' is required/i)
    ).toBeInTheDocument();
  });
});
