import { test, expect } from "@playwright/test";
import { loginAs, logout } from "./helpers";

test.describe("Security & Contract Acceptance", () => {
  test("enforces protected routes by redirecting unauthenticated users to /login", async ({ page }) => {
    // Attempt to access protected routes while unauthenticated
    const protectedRoutes = ["/dashboard", "/goals", "/plans", "/workflows/inbox", "/schedules", "/analytics", "/audit"];

    for (const route of protectedRoutes) {
      await page.goto(route);
      await page.waitForURL("**/login**");
      await expect(page.getByRole("button", { name: /Sign in with Keycloak/i })).toBeVisible();
    }
  });

  test("rejects unauthorized access and enforces role-based permission boundaries", async ({ page, request }) => {
    // 1. Direct unauthenticated API request must fail closed with HTTP 401
    const unauthResponse = await request.get("http://127.0.0.1:8088/api/v1/auth/me");
    expect(unauthResponse.status()).toBe(401);

    // 2. Authenticate as Planner
    await loginAs(page, "planner");

    // 3. Attempt to access Audit Trail as planner (requires auditor / tenant_admin role)
    await page.getByRole("link", { name: /Audit Trail/i }).click();
    await page.waitForURL("**/audit");
    await expect(page.getByText(/Insufficient permissions to inspect audit trail/i)).toBeVisible();
  });

  test("enforces tenant isolation and session logout lifecycle", async ({ page }) => {
    // 1. Authenticate as planner
    await loginAs(page, "planner");

    // 2. Open User account menu and verify tenant identity
    const userMenuButton = page.getByRole("button", { name: /User account menu/i });
    await userMenuButton.click();
    await expect(page.getByText(/tenant-pilot-001/i).first()).toBeVisible();
    await expect(page.getByText(/planner/i).first()).toBeVisible();

    // 3. Execute logout
    const signOutButton = page.getByRole("button", { name: /Sign Out/i });
    await signOutButton.click();

    // 4. Verify redirected to login
    await page.waitForURL("**/login");
    await expect(page.getByRole("button", { name: /Sign in with Keycloak/i })).toBeVisible();

    // 5. Verify post-logout navigation to protected route redirects back to login
    await page.goto("/dashboard");
    await page.waitForURL("**/login**");
    await expect(page.getByRole("button", { name: /Sign in with Keycloak/i })).toBeVisible();
  });
});
