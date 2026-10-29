import { Page, expect } from "@playwright/test";
import { execSync } from "child_process";
import * as path from "path";

export type Persona = "planner" | "approver" | "scheduler" | "admin";

/**
 * Authenticates via Keycloak Authorization Code + PKCE against disposable OIDC IDP (Port 8180).
 * Exercises genuine browser redirects, cryptographic asymmetric RS256 token exchange,
 * and server-side OIDC verification on the actual FastAPI application (Port 8000).
 */
export async function loginAs(page: Page, persona: Persona): Promise<void> {
  await page.goto("/login");

  // Locate Keycloak Demo SSO CTA
  const ssoButton = page.getByRole("button", { name: /Sign in with Keycloak/i });
  await expect(ssoButton).toBeVisible();
  await ssoButton.click();

  // Redirects to Keycloak Demo OIDC Identity Provider (Port 8180)
  await expect(page).toHaveURL(/.*8180.*openid-connect\/auth.*/);
  const personaButton = page.locator(`#btn-login-${persona}`);
  await expect(personaButton).toBeVisible();
  await personaButton.click();

  // Redirects back to frontend with authorization code, completes token exchange, lands on /dashboard
  await page.waitForURL("**/dashboard", { timeout: 20000 });
  await expect(page.getByRole("heading", { name: /Operations Overview/i })).toBeVisible();
}

/**
 * Authenticated logout via user account menu.
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

export interface DbVerifyResult {
  success: boolean;
  count?: number;
  records?: Array<Record<string, unknown>>;
  error?: string;
}

/**
 * Direct PostgreSQL persistence verification via scripts/verify_db_state.py.
 */
export function verifyDb(command: string, args: Record<string, string>): DbVerifyResult {
  const repoRoot = path.resolve(__dirname, "../../..");
  const isWindows = process.platform === "win32";
  const pythonCmd = isWindows ? ".venv\\Scripts\\python.exe" : "python";

  const argPairs = Object.entries(args)
    .map(([k, v]) => `--${k} "${v}"`)
    .join(" ");

  const fullCmd = `${pythonCmd} scripts/verify_db_state.py ${command} ${argPairs}`;

  try {
    const stdout = execSync(fullCmd, {
      cwd: repoRoot,
      encoding: "utf-8",
      env: {
        ...process.env,
        PYTHONPATH: repoRoot,
      },
    });
    return JSON.parse(stdout) as DbVerifyResult;
  } catch (err: unknown) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    return { success: false, error: errorMsg };
  }
}

