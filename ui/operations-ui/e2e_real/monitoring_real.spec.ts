import { test, expect } from "@playwright/test";
import { loginAs, verifyDb } from "./helpers";

test.describe("Real Application Monitoring Journey (Admin Persona)", () => {
  test("reviews operational analytics telemetry and verifies immutable audit trail in PostgreSQL", async ({ page }) => {
    // 1. Authenticate as authorized admin
    await loginAs(page, "admin");

    // 2. Open Operations Analytics
    await page.getByRole("link", { name: /Operations Analytics/i }).click();
    await page.waitForURL("**/analytics");
    await expect(page.getByRole("heading", { name: /Operations Analytics/i })).toBeVisible();

    // 3. Verify analytics telemetry cards
    await expect(page.getByText(/Plan Progress/i).first()).toBeVisible();
    await expect(page.getByText(/Schedule Health/i).first()).toBeVisible();
    await expect(page.getByText(/SLA Compliance|SLA State/i).first()).toBeVisible();

    // 4. Open Enterprise Audit Explorer
    await page.getByRole("link", { name: /Audit Trail/i }).click();
    await page.waitForURL("**/audit");
    await expect(page.getByRole("heading", { name: /Enterprise Audit Explorer/i })).toBeVisible();

    // 5. Verify recorded immutable audit actions
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.getByText(/SYSTEM_INITIALIZED/i).first()).toBeVisible();

    // 6. Direct PostgreSQL audit event verification
    const auditDb = verifyDb("verify-audit", { "event-type": "SYSTEM_INITIALIZED" });
    expect(auditDb.success).toBe(true);
    expect(auditDb.count).toBeGreaterThanOrEqual(1);
  });
});
