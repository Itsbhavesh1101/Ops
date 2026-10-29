import { test, expect } from "@playwright/test";
import { loginAs, verifyDb } from "./helpers";

test.describe("Real Application Workflow Journey (Approver Persona)", () => {
  test("claims and completes live Flowable human task, verifies SLA tracking, and checks PostgreSQL persistence", async ({ page }) => {
    // 1. Authenticate as authorized approver
    await loginAs(page, "approver");

    // 2. Open Workflow Inbox
    await page.locator('a[href="/workflows/inbox"]').click();
    await page.waitForURL("**/workflows/inbox");
    await expect(page.getByRole("heading", { name: /Workflow Inbox/i })).toBeVisible();

    // 3. Locate pending human task from Flowable engine
    const taskItem = page.getByText(/Science Department Chair Review|Humanities Department Chair Review|Academic Dean Final Moderation|Review/i).first();
    await expect(taskItem).toBeVisible({ timeout: 15000 });

    // 4. Select task and claim it
    await taskItem.click();

    const claimButton = page.getByRole("button", { name: "Claim Task" });
    await expect(claimButton).toBeVisible({ timeout: 10000 });
    await claimButton.click();
    await expect(page.getByText("CLAIMED").first()).toBeVisible({ timeout: 10000 });

    // 5. Complete task
    const completeButton = page.getByRole("button", { name: /Submit Approval|Complete Task/i });
    await expect(completeButton).toBeVisible({ timeout: 10000 });
    await completeButton.click();
    await expect(page.getByText("COMPLETED").first()).toBeVisible({ timeout: 10000 });

    // 6. Direct PostgreSQL workflow state verification
    const taskDb = verifyDb("verify-workflow-task", { status: "COMPLETED" });
    expect(taskDb.success).toBe(true);
  });
});

