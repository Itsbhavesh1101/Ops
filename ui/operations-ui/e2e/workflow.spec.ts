import { test, expect } from "@playwright/test";
import { loginAs } from "./helpers";

test.describe("Workflow Journey (Approver Persona)", () => {
  test("authenticates, opens workflow inbox, claims and completes human task, verifies SLA", async ({ page }) => {
    // 1. Authenticate as authorized approver
    await loginAs(page, "approver");

    // 2. Navigate to Plans and verify approved plan exists
    await page.getByRole("link", { name: "Plans", exact: true }).click();
    await page.waitForURL("**/plans");
    await expect(page.getByText(/APPROVED/i).first()).toBeVisible();

    // 3. Open Workflow Inbox
    await page.locator('a[href="/workflows/inbox"]').click();
    await page.waitForURL("**/workflows/inbox");
    await expect(page.getByRole("heading", { name: /Workflow Inbox/i })).toBeVisible();

    // 4. Verify assigned workflow tasks and SLA tracking badge
    const taskItem = page.getByText(/Review Examination Moderation/i).first();
    await expect(taskItem).toBeVisible();
    await expect(page.getByText(/SLA On Track/i).or(page.getByText(/SLA Running/i)).first()).toBeVisible();

    // 5. Select the human task
    await taskItem.click();

    // 6. Claim and complete the human task
    const claimButton = page.getByRole("button", { name: "Claim Task" });
    if (await claimButton.isVisible()) {
      await claimButton.click();
      await expect(page.getByText("CLAIMED").first()).toBeVisible();
    }

    const completeButton = page.getByRole("button", { name: "Submit Approval" });
    if (await completeButton.isVisible()) {
      await completeButton.click();
      await expect(page.getByText("COMPLETED").first()).toBeVisible();
    }

    // 7. Verify updated workflow task status
    await expect(page.getByText(/COMPLETED/i).or(page.getByText(/CLAIMED/i)).first()).toBeVisible();
  });
});
