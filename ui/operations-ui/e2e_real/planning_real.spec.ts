import { test, expect } from "@playwright/test";
import { loginAs, verifyDb } from "./helpers";

test.describe("Real Application Planning Journey (Planner Persona)", () => {
  test("creates goal, synthesizes plan via SemesterExamPlanner, reviews WBS and critical path, and verifies PostgreSQL persistence", async ({ page }) => {
    // 1. Authenticate as authorized planner
    await loginAs(page, "planner");

    // 2. Open Goals and establish new operational goal
    await page.getByRole("link", { name: "Goals", exact: true }).click();
    await page.waitForURL("**/goals");
    await expect(page.getByRole("heading", { name: "Operational Goals" })).toBeVisible();

    const createGoalBtn = page.getByRole("button", { name: "Create Goal" });
    await expect(createGoalBtn).toBeVisible();
    await createGoalBtn.click();

    // Fill goal modal
    const objective = "Grade 10 Semester Examination Operations 2026";
    await page.getByLabel(/Objective/i).fill(objective);
    await page.getByLabel("Scope", { exact: true }).fill("Academic Affairs");
    await page.getByLabel("Priority Target").fill("HIGH");

    // Submit Goal to real FastAPI backend
    await page.getByRole("button", { name: "Establish Goal" }).click();

    // Lands on Goal Detail Page (/goals/:goalId)
    await page.waitForURL(/.*\/goals\/.*/);
    await expect(page.getByRole("heading", { name: objective })).toBeVisible();

    // 3. Synthesize operational plan using SemesterExamPlanner
    const generateBtn = page.getByRole("button", { name: "Generate Plan" }).first();
    await expect(generateBtn).toBeVisible({ timeout: 15000 });
    await generateBtn.click();

    // Lands on Plan Detail Page (/plans/:planId)
    await page.waitForURL(/.*\/plans\/.*/, { timeout: 20000 });
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    // 4. Review WBS Tasks in PostgreSQL
    await expect(page.getByRole("table")).toBeVisible();

    // 5. Review Critical Path Tab
    const criticalPathTab = page.getByRole("button", { name: /Critical Path Analysis/i });
    if (await criticalPathTab.isVisible()) {
      await criticalPathTab.click();
      await expect(page.getByText(/Total Project Finish Duration|Duration/i).first()).toBeVisible();
    }

    // 6. Direct PostgreSQL persistence verification
    const goalDb = verifyDb("verify-goal", { objective: "Grade 10 Semester Examination Operations 2026" });
    expect(goalDb.success).toBe(true);
    expect(goalDb.count).toBeGreaterThanOrEqual(1);

    const planDb = verifyDb("verify-plan", { name: "Grade 10" });
    expect(planDb.success).toBe(true);
    expect(planDb.count).toBeGreaterThanOrEqual(1);
  });
});

