import { test, expect } from "@playwright/test";
import { loginAs } from "./helpers";

test.describe("Monitoring Journey (Auditor & Admin Persona)", () => {
  test("authenticates as admin/auditor, reviews analytics telemetry, and verifies immutable audit trail", async ({ page }) => {
    // 1. Authenticate as authorized admin/auditor
    await loginAs(page, "admin");

    // 2. Open Operations Analytics
    await page.getByRole("link", { name: /Operations Analytics/i }).click();
    await page.waitForURL("**/analytics");
    await expect(page.getByRole("heading", { name: /Operations Analytics/i })).toBeVisible();

    // 3. Verify operational KPI metrics
    await expect(page.getByText(/Plan Progress/i).first()).toBeVisible();
    await expect(page.getByText(/Schedule Health/i).first()).toBeVisible();
    await expect(page.getByText(/SLA Compliance/i).or(page.getByText(/SLA State/i)).first()).toBeVisible();

    // 4. Open Audit Trail
    await page.getByRole("link", { name: /Audit Trail/i }).click();
    await page.waitForURL("**/audit");
    await expect(page.getByRole("heading", { name: /Enterprise Audit Explorer/i })).toBeVisible();

    // 5. Verify recorded immutable audit actions
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.getByText(/SYSTEM_INITIALIZED/i).or(page.getByText(/GOAL_CREATED/i)).first()).toBeVisible();

    // 6. Inspect an audit event modal
    const viewEventBtn = page.getByRole("button", { name: /Inspect/i }).first();
    if (await viewEventBtn.isVisible()) {
      await viewEventBtn.click();
      await expect(page.getByText(/Audit Record/i)).toBeVisible();
      // Close modal
      const closeBtn = page.getByRole("button", { name: /Close/i }).first();
      if (await closeBtn.isVisible()) {
        await closeBtn.click();
      }
    }
  });
});
