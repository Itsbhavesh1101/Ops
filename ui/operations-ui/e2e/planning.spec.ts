import { test, expect } from "@playwright/test";
import { loginAs } from "./helpers";

test.describe("Planning Journey (Planner Persona)", () => {
  test("authenticates, creates goal, generates WBS plan, reviews critical path, and reloads", async ({ page }) => {
    // 1. Authenticate as authorized planner
    await loginAs(page, "planner");

    // 2. Open dashboard and verify metrics
    await expect(page.getByRole("heading", { name: /Operations Overview/i })).toBeVisible();
    await expect(page.getByText(/Operations Engine/i).first()).toBeVisible();

    // 3. Navigate to Goals and create an operational goal
    await page.getByRole("link", { name: "Goals", exact: true }).click();
    await page.waitForURL("**/goals");
    await expect(page.getByRole("heading", { name: "Operational Goals", exact: true })).toBeVisible();

    const createGoalBtn = page.getByRole("button", { name: "Create Goal" });
    await expect(createGoalBtn).toBeVisible();
    await createGoalBtn.click();

    // Fill in Goal Creation Modal
    const objectiveInput = page.getByLabel(/Objective/i);
    await expect(objectiveInput).toBeVisible();
    await objectiveInput.fill("Automated E2E Semester Exam Operations");

    const scopeInput = page.getByLabel("Scope", { exact: true });
    await scopeInput.fill("Academic Affairs");

    const priorityInput = page.getByLabel("Priority Target");
    await priorityInput.fill("HIGH");

    // Submit Goal
    const submitGoalBtn = page.getByRole("button", { name: "Establish Goal" });
    await submitGoalBtn.click();

    // Verify Goal is created and visible
    await expect(page.getByRole("heading", { name: "Automated E2E Semester Exam Operations" })).toBeVisible();

    // 4. Navigate to Plans
    await page.getByRole("link", { name: "Plans", exact: true }).click();
    await page.waitForURL("**/plans");
    await expect(page.getByRole("heading", { name: "Operational Plans", exact: true })).toBeVisible();

    // 5. Open an operational plan to review tasks and dependencies
    const viewPlanLink = page.getByRole("link", { name: /Grade 10 Semester Examination Operations/i }).first();
    await expect(viewPlanLink).toBeVisible();
    await viewPlanLink.click();

    await page.waitForURL(/.*\/plans\/.*/);
    await expect(page.getByRole("heading", { name: /Grade 10 Semester Examination Operations/i })).toBeVisible();

    // 6. Review WBS Tasks
    await expect(page.getByText(/Prepare Exam Materials/i)).toBeVisible();

    // 7. Review Critical Path Tab
    const criticalPathTab = page.getByRole("button", { name: /Critical Path Analysis/i });
    await expect(criticalPathTab).toBeVisible();
    await criticalPathTab.click();
    await expect(page.getByText("Total Project Finish Duration")).toBeVisible();

    // 8. Review Milestones Tab
    const milestonesTab = page.getByRole("button", { name: /Milestones/i });
    await expect(milestonesTab).toBeVisible();
    await milestonesTab.click();
    await expect(page.getByText("Preparation Milestone").first()).toBeVisible();

    // 9. Save and reload the plan
    const refreshBtn = page.getByRole("button", { name: "Refresh", exact: true });
    await refreshBtn.click();
    await expect(page.getByRole("heading", { name: /Grade 10 Semester Examination Operations/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Critical Path Analysis/i })).toBeVisible();
  });
});
