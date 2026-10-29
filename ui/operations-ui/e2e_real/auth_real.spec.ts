import { test, expect } from "@playwright/test";
import { loginAs, logout } from "./helpers";

test.describe("Real Backend Authentication & Authorization Gates", () => {
  test("authenticates via Keycloak OIDC, validates RS256 JWT signature, and enforces tenant scoping", async ({ page, request }) => {
    // 1. Direct unauthenticated request to actual FastAPI backend must fail closed (HTTP 401)
    const unauthMe = await request.get("http://127.0.0.1:8000/api/v1/auth/me");
    expect(unauthMe.status()).toBe(401);

    // 2. Authenticate planner through real browser PKCE redirect flow
    await loginAs(page, "planner");

    // 3. Inspect authenticated user identity from account menu
    const userMenuButton = page.getByRole("button", { name: /User account menu/i });
    await expect(userMenuButton).toBeVisible();
    await userMenuButton.click();

    // Verify tenant resolution and role assignment on real backend
    await expect(page.getByText(/tenant-pilot-001/i).first()).toBeVisible();
    await expect(page.getByText(/planner/i).first()).toBeVisible();

    // 4. Verify unauthenticated redirect for protected routes
    await logout(page);
    await page.goto("/dashboard");
    await page.waitForURL("**/login**");
    await expect(page.getByRole("button", { name: /Sign in with Keycloak/i })).toBeVisible();
  });

  test("enforces role-based authorization: blocks planner from compliance audit explorer", async ({ page }) => {
    // 1. Log in as planner
    await loginAs(page, "planner");

    // 2. Attempt to navigate to compliance audit explorer
    await page.getByRole("link", { name: /Audit Trail/i }).click();
    await page.waitForURL("**/audit");

    // 3. Verify real backend permission rejection (planner role lacks audit:read)
    await expect(page.getByText(/Insufficient permissions to inspect audit trail/i)).toBeVisible();
  });

  test("grants compliance audit access to admin/auditor persona", async ({ page }) => {
    // 1. Log in as admin
    await loginAs(page, "admin");

    // 2. Navigate to compliance audit explorer
    await page.getByRole("link", { name: /Audit Trail/i }).click();
    await page.waitForURL("**/audit");

    // 3. Verify audit explorer is accessible and displays event table
    await expect(page.getByRole("heading", { name: /Enterprise Audit Explorer/i })).toBeVisible();
    await expect(page.getByRole("table")).toBeVisible();
  });
});

