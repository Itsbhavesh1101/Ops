import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e_real",
  timeout: 60000,
  expect: {
    timeout: 15000,
  },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
      },
    },
  ],
  webServer: [
    {
      // 1. Standalone Keycloak Demo OIDC Identity Provider Emulator
      command: process.platform === "win32"
        ? ".venv\\Scripts\\python.exe tests/e2e/browser/oidc_idp_server.py"
        : "python tests/e2e/browser/oidc_idp_server.py",
      url: "http://127.0.0.1:8180/ready",
      reuseExistingServer: false,
      timeout: 30000,
      cwd: "../..",
    },
    {
      // 2. Real Production FastAPI Operations Engine Backend (app.main:app)
      command: process.platform === "win32"
        ? ".venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
        : "python -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
      url: "http://127.0.0.1:8000/api/v1/health",
      reuseExistingServer: false,
      timeout: 45000,
      cwd: "../..",
      env: {
        E7_ENVIRONMENT: "local",
        E7_MOUNT_DOMAIN_ROUTERS: "true",
        E7_OIDC_PROVIDER_NAME: "keycloak",
        E7_OIDC_ISSUER_URL: "http://127.0.0.1:8180/realms/operations-engine-demo",
        E7_OIDC_DISCOVERY_URL: "http://127.0.0.1:8180/realms/operations-engine-demo/.well-known/openid-configuration",
        E7_OIDC_CLIENT_IDS: '["operations-ui"]',
        E7_OIDC_ALLOWED_ISSUERS: '["http://127.0.0.1:8180/realms/operations-engine-demo"]',
        E7_OIDC_ALLOWED_JWKS_HOSTS: '["127.0.0.1", "localhost"]',
        E7_OIDC_DEFAULT_TENANT_ID: "tenant-pilot-001",
        E7_CORS_ALLOWED_ORIGINS: '["http://localhost:5173", "http://127.0.0.1:5173"]',
        E7_DATABASE_URL: process.env.E7_DATABASE_URL || "postgresql+asyncpg://postgres:postgres@localhost:5432/operations_engine",
        E7_RABBITMQ_URL: process.env.E7_RABBITMQ_URL || "amqp://guest:guest@localhost:5672/",
        FLOWABLE_BASE_URL: process.env.FLOWABLE_BASE_URL || "http://localhost:8080/flowable-rest/service",
        FLOWABLE_USER: process.env.FLOWABLE_USER || "rest-admin",
        FLOWABLE_PASSWORD: process.env.FLOWABLE_PASSWORD || "test",
        FLOWABLE_TIMEOUT_SECONDS: "10.0",
        FLOWABLE_MAX_RETRIES: "3",
      },
    },
    {
      // 3. React / Vite Frontend
      command: "npm run dev",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 30000,
      cwd: ".",
      env: {
        VITE_DEMO_LOCAL_OIDC: "true",
        VITE_OIDC_PROVIDER: "keycloak",
        VITE_KEYCLOAK_URL: "http://127.0.0.1:8180",
        VITE_API_BASE_URL: "http://127.0.0.1:8000/api/v1",
        VITE_DEFAULT_TENANT_ID: "tenant-pilot-001",
      },
    },
  ],
});

