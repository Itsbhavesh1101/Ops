import { Page, expect } from "@playwright/test";

export type Persona = "planner" | "approver" | "scheduler" | "admin";

/**
 * Executes authentic Keycloak Authorization Code + PKCE login flow.
 * No mock injection or bypass; exercises real browser redirects,
 * token exchange, and cryptographic verification on backend.
 */
export async function loginAs(page: Page, persona: Persona): Promise<void> {
  await page.goto("/login");

  // Locate the Keycloak Demo SSO CTA
  const ssoButton = page.getByRole("button", { name: /Sign in with Keycloak/i });
  await expect(ssoButton).toBeVisible();
  await ssoButton.click();

  // Redirects to Keycloak Demo OIDC Identity Provider (Port 8180)
  await expect(page).toHaveURL(/.*8180.*openid-connect\/auth.*/);
  const personaButton = page.locator(`#btn-login-${persona}`);
  await expect(personaButton).toBeVisible();
  await personaButton.click();

  // Redirects back to frontend with authorization code and lands on /dashboard
  await page.waitForURL("**/dashboard", { timeout: 15000 });
  await expect(page.getByRole("heading", { name: /Operations Overview/i })).toBeVisible();
}

/**
 * Executes authenticated logout via user account menu.
 */
export async function logout(page: Page): Promise<void> {
  const userMenuButton = page.getByRole("button", { name: /User account menu/i });
  await expect(userMenuButton).toBeVisible();
  await userMenuButton.click();

  const signOutButton = page.getByRole("button", { name: /Sign Out/i });
  await expect(signOutButton).toBeVisible();
  await signOutButton.click();

  await page.waitForURL("**/login", { timeout: 10000 });
  await expect(page.getByRole("button", { name: /Sign in with Keycloak/i })).toBeVisible();
}
