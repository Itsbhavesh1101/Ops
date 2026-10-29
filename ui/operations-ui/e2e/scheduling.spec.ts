import { test, expect } from "@playwright/test";
import { loginAs } from "./helpers";

test.describe("Scheduling Journey (Scheduler Persona)", () => {
  test("authenticates, solves feasible timetable, reviews assignments, and publishes schedule", async ({ page }) => {
    // 1. Authenticate as authorized scheduler
    await loginAs(page, "scheduler");

    // 2. Navigate to Scheduling Jobs
    await page.locator('a[href="/scheduling/jobs"]').click();
    await page.waitForURL("**/scheduling/jobs");
    await expect(page.getByRole("heading", { name: "Scheduling Jobs", exact: true })).toBeVisible();

    // 3. Submit a new scheduling solver job
    const runSolverBtn = page.getByRole("button", { name: "Run Scheduler" }).first();
    await expect(runSolverBtn).toBeVisible();
    await runSolverBtn.click();

    // Submit in modal
    const confirmSolveBtn = page.getByLabel("Run CP-SAT Solver");
    await expect(confirmSolveBtn).toBeVisible();
    await confirmSolveBtn.click();

    // 4. Verify solver job solves to FEASIBLE
    await expect(page.getByText(/FEASIBLE/i).first()).toBeVisible({ timeout: 15000 });

    // 5. Navigate to Timetables & Schedules
    await page.getByRole("link", { name: "Timetables & Schedules" }).click();
    await page.waitForURL("**/schedules");
    await expect(page.getByRole("heading", { name: "Timetables & Schedules", exact: true })).toBeVisible();

    // 6. Open schedule detail
    const viewScheduleBtn = page.getByRole("link", { name: /View Schedule/i }).first();
    await expect(viewScheduleBtn).toBeVisible();
    await viewScheduleBtn.click();

    await page.waitForURL(/.*\/schedules\/.*/);
    await expect(page.getByText(/Assignments|Timetable Matrix/i).first()).toBeVisible();

    // 7. Verify assignments, rooms, teachers
    await expect(page.getByText(/Exam Hall/i).or(page.getByText(/T1_EXAM_PREP/i)).first()).toBeVisible();

    // 8. Publish the schedule
    const publishBtn = page.getByRole("button", { name: /Publish Schedule/i }).first();
    await expect(publishBtn).toBeVisible();
    await publishBtn.click();

    // Confirm dialog appears
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    const confirmBtn = dialog.getByRole("button", { name: "Publish Timetable" });
    await confirmBtn.click();

    await expect(page.getByText(/PUBLISHED/i).first()).toBeVisible({ timeout: 10000 });

    // 9. Verify persisted status by navigating to timetable and reviewing persisted schedule
    await page.getByRole("link", { name: "Timetables & Schedules" }).click();
    await page.waitForURL("**/schedules");
    await expect(page.getByText(/PUBLISHED/i).first()).toBeVisible();
  });
});
