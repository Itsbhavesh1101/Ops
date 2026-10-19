# IR Infotech E-7 Operations Engine — Frontend (operations-ui)

Canonical enterprise user interface for the IR Infotech E-7 Operations Engine.

## 1. Directory & Stack Overview

- **Canonical Frontend Path**: `ui/operations-ui`
- **Framework**: React 18.3.1
- **Language**: TypeScript 5.7.3 (strict mode, zero-any policy)
- **Bundler & Tooling**: Vite 6.1.0
- **Routing**: React Router DOM 6.30.6
- **Styling**: Vanilla CSS Design Token Architecture (`src/index.css`)
- **Icons**: Lucide React 0.475.0
- **Testing**: Vitest 3.2.7 + React Testing Library + jsdom
- **Package Manager**: npm (reproducible install via `package-lock.json`)

## 2. Prerequisites

- Node.js >= 18.0.0
- npm >= 9.0.0
- Running E-7 Operations Engine Backend Gateway (`http://127.0.0.1:8088`)

## 3. Installation

From the frontend directory (`ui/operations-ui`):

```bash
cd ui/operations-ui
npm ci
```

## 4. Environment Variables

Create `.env.local` if custom configuration is required. Default development fallback is pre-configured:

| Variable | Default Value | Description |
|---|---|---|
| `VITE_API_BASE_URL` | `http://127.0.0.1:8088/api/v1` | Base URL for Operations Engine backend API gateway |

## 5. Development & Build Commands

- **Start Development Server** (Runs on port `5173` to match backend CORS policy):
  ```bash
  npm run dev
  ```
- **Run Unit & Component Tests**:
  ```bash
  npm test
  ```
- **Run TypeScript Verification**:
  ```bash
  npm run typecheck
  ```
- **Run ESLint**:
  ```bash
  npm run lint
  ```
- **Build Production Bundle**:
  ```bash
  npm run build
  ```

## 6. Authentication Architecture & Security Policy

The frontend integrates directly with the verified backend authentication boundary:

- **Canonical Identity Lookup**: `GET /api/v1/auth/me`
- **Human Browser Authentication**: Exclusively Google Workspace OpenID Connect (OIDC) ID-tokens passed via standard `Authorization: Bearer <id_token>` headers.
- **Service API Key Policy**: Machine-to-machine service credentials (`e7sk_...`) are strictly prohibited in the browser application. The frontend runtime never stores, parses, or transmits `X-E7-Service-Key`.
- **Token Storage Policy**: In-memory token state only. Zero persistence of credentials in `localStorage`, `sessionStorage`, or `IndexedDB`.
- **External Configuration Status**:
  - `OIDC_CLIENT_ID_REQUIRED`: Google OAuth 2.0 Web Client ID corresponding to audience `e7-pilot-client-id`.
  - `OIDC_REDIRECT_URI`: `http://127.0.0.1:5173`
  - `OIDC_ISSUER_OR_PROVIDER`: `https://accounts.google.com`
  - `ALLOWED_ORIGIN`: `http://127.0.0.1:5173`, `http://localhost:5173`
  - `LOGOUT_REDIRECT_REQUIREMENT`: In-memory session clear and redirect to `/login`
  - `HUMAN_BROWSER_AUTH = BLOCKED_ON_COMPANY_OIDC_CLIENT_CONFIGURATION` (truthful external provisioning status; does not block UI development).
- **Zero Mock Bypass**: Unauthenticated sessions are redirected to `/login`. No mock personas or hardcoded secrets are bundled.

## 7. Application Route Map

| Path | Component / Screen | Status | Description |
|---|---|---|---|
| `/login` | `LoginPage` | LIVE (F1) | Public authentication screen verifying against `/api/v1/auth/me` |
| `/` | Redirect to `/dashboard` | LIVE (F1) | Root default redirect |
| `/dashboard` | `DashboardPage` | LIVE (F1) | Real-time Operations Dashboard consuming `/analytics/operations` |
| `/goals` | `PlaceholderPage` | Phase F2 | Operational goal hierarchy and KPI decomposition |
| `/goals/:goalId` | `PlaceholderPage` | Phase F2 | Sub-goal breakdown and metric allocations |
| `/plans` | `PlaceholderPage` | Phase F2 | Automated plan synthesis and step sequencing |
| `/plans/:planId` | `PlaceholderPage` | Phase F2 | Plan detail and Gantt inspection view |
| `/tasks` | `PlaceholderPage` | Phase F2 | Work breakdown structure (WBS) and predecessor graph |
| `/workflows` | Redirect | LIVE (F1) | Redirects to `/workflows/inbox` |
| `/workflows/inbox` | `TaskInbox` | LIVE (F1) | Human task approval inbox |
| `/workflows/bpmn` | `BpmnDesigner` | LIVE (F1) | BPMN 2.0 visual process modeler |
| `/workflows/dmn` | `DmnEditor` | LIVE (F1) | DMN decision table editor and runtime simulation |
| `/workflows/forms` | `FormEditor` | LIVE (F1) | Dynamic schema form builder |
| `/workflows/:instanceId` | `PlaceholderPage` | Phase F3 | Process token tracing and variable inspector |
| `/resources` | `PlaceholderPage` | Phase F2 | Resource inventory, equipment, and facility catalog |
| `/resources/availability` | `PlaceholderPage` | Phase F2 | Resource shift calendars, blackout windows, and quotas |
| `/scheduling/jobs` | `PlaceholderPage` | Phase F2 | Asynchronous solver orchestration and solver logs |
| `/schedules` | `PlaceholderPage` | Phase F2 | Feasible timetable schedules and conflict inspection |
| `/schedules/:scheduleId` | `PlaceholderPage` | Phase F2 | Assignment matrix and constraint satisfaction proof |
| `/analytics` | `PlaceholderPage` | Phase F3 | Longitudinal throughput and SLA heatmaps |
| `/audit` | `PlaceholderPage` | Phase F3 | Immutable security logs and state transitions |
| `/system` | `SystemPage` | LIVE (F1) | System health (`/health`) and readiness (`/ready`) telemetry |
| `/forbidden` | `ForbiddenPage` | LIVE (F1) | 403 Forbidden role/permission denial state |
| `*` | `NotFoundPage` | LIVE (F1) | 404 Route Not Found fallback |
