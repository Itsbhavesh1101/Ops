import { test, expect } from "@playwright/test";
import { loginAs, verifyDb } from "./helpers";

test.describe("Real Application Scheduling Journey (Scheduler Persona)", () => {
  test("submits CP-SAT solver job, reviews feasible timetable, publishes schedule, and verifies PostgreSQL persistence", async ({ page }, testInfo) => {
    // 1. Authenticate as authorized scheduler
    await loginAs(page, "scheduler");

    // 2. Open Scheduling Jobs
    await page.locator('a[href="/scheduling/jobs"]').click();
    await page.waitForURL("**/scheduling/jobs");
    await expect(page.getByRole("heading", { name: "Scheduling Jobs", exact: true })).toBeVisible();

    // 3. Submit scheduling solver job
    const runSolverBtn = page.getByRole("button", { name: /Run Scheduler|Dispatch First Job/i }).first();
    await expect(runSolverBtn).toBeVisible();
    await runSolverBtn.click();

    const scheduleKey = `e2e-schedule-${testInfo.workerIndex}-${Date.now()}`;
    await page.getByPlaceholder("e.g. sem1-finals-hall-alpha").fill(scheduleKey);

    const confirmSolveBtn = page.getByLabel("Run CP-SAT Solver");
    await expect(confirmSolveBtn).toBeVisible();
    await confirmSolveBtn.click();

    // 4. Verify solver completes to FEASIBLE
    await expect(page.getByText(/FEASIBLE/i).first()).toBeVisible({ timeout: 20000 });

    // 5. Open the timetable produced by this exact job. The schedule list can
    // contain previously published acceptance schedules from earlier runs.
    const openTimetableBtn = page.getByRole("link", { name: /Open Timetable/i });
    await expect(openTimetableBtn).toBeVisible();
    await openTimetableBtn.click();

    await page.waitForURL(/.*\/schedules\/.*/);
    await expect(page.getByText(scheduleKey, { exact: false }).first()).toBeVisible();
    await expect(page.getByText(/Assignments|Timetable Matrix/i).first()).toBeVisible();

    // 6. Publish schedule
    const publishBtn = page.getByRole("button", { name: /Publish Schedule/i }).first();
    await expect(publishBtn).toBeVisible();
    await publishBtn.click();

    // Confirm dialog
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    const confirmBtn = dialog.getByRole("button", { name: "Publish Timetable" });
    await confirmBtn.click();

    await expect(page.getByText(/PUBLISHED/i).first()).toBeVisible({ timeout: 15000 });

    // 7. Direct PostgreSQL persistence verification
    const schedDb = verifyDb("verify-schedule", { status: "PUBLISHED" });
    expect(schedDb.success).toBe(true);
    expect(schedDb.count).toBeGreaterThanOrEqual(1);
  });
});

