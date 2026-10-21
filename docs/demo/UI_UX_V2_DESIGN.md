# Operations Engine Demo Interface V2 — Modern UI/UX Redesign

**Specification & Architecture Document**
**Repository**: `IR-INFOTECH/e7_operations_engine`
**Interface**: `ui/operations-ui` (React 18 + TypeScript + Vite)
**Design Standard**: Modern Calm B2B SaaS (Linear / Stripe / Vercel Aesthetic)
**State**: Verification Complete & Uncommitted for Visual Review

---

## 1. Executive Summary

This document describes the **V2 Modern UI/UX Redesign** for the IR Infotech E-7 Operations Engine Demo Interface.

The objective of this phase was to elevate the user experience from an initial functional prototype into a polished, production-grade enterprise B2B SaaS interface. The redesign establishes a calm, cohesive aesthetic with crisp visual hierarchy, high information density, clear operational workflows, and zero extraneous visual clutter—while strictly adhering to the architectural and security invariants of the frozen backend release candidate.

### Core Redesign Invariants Maintained:
- **Zero Feature Loss**: All existing workflows, buttons, modals, and lifecycle operations remain fully operational.
- **Zero Backend Changes**: The backend code (`app/*`), test suites (`tests/*`), and container definitions (`docker/*`, `docker-compose*.yml`) remain 100% untouched (`git diff -- app tests docker` is completely empty).
- **Zero Mock Data**: All views connect to the real PostgreSQL database, Keycloak identity provider, RabbitMQ message broker, and Nginx gateway.
- **Zero Auth Bypass**: Real OpenID Connect (OIDC) Authorization Code Flow with PKCE (S256) is preserved; machine-to-machine service credentials remain strictly excluded from browser interfaces.
- **Stack Integrity**: Built with React 18, TypeScript, React Router v6, Lucide icons, and modern design tokens; zero massive UI runtime libraries (no Tailwind runtime, MUI, AntD, or Bootstrap) were added.
- **Zero Uncommitted Commits/Pushes/Tags**: All changes remain uncommitted in the working tree for visual review.

---

## 2. Design System & Token Foundation

The design system is implemented in `ui/operations-ui/src/index.css` via structured CSS Custom Properties, providing a single source of truth across surfaces, borders, typography, and semantic feedback.

### 2.1 Surface & Border Tokens
```css
:root {
  /* Surface Tokens (Calm, crisp B2B SaaS palette) */
  --bg-app: #f8fafc;           /* Slate 50: off-white background */
  --bg-sidebar: #ffffff;       /* Pure white sidebar */
  --bg-header: #ffffff;        /* Pure white sticky header */
  --bg-card: #ffffff;          /* Pure white card surfaces */
  --bg-card-hover: #f8fafc;    /* Subtle hover state */
  --bg-card-secondary: #f1f5f9;/* Slate 100 secondary surface */
  --bg-input: #ffffff;         /* Pure white inputs */

  /* Border Tokens */
  --border-subtle: #e2e8f0;    /* Slate 200: clean subtle dividers */
  --border-default: #cbd5e1;   /* Slate 300: active input borders */
  --border-focus: #4f46e5;     /* Indigo 600: accessible focus ring */
}
```

### 2.2 Typography & Semantic Tokens
- **Font Stack**: System UI (`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`) with monospace tabular numerals (`--font-mono: ui-monospace, SFMono-Regular, Menlo, monospace`).
- **Text Palette**:
  - Primary text: `--text-primary: #0f172a` (Slate 900)
  - Secondary text: `--text-secondary: #334155` (Slate 700)
  - Muted/labels: `--text-muted: #64748b` (Slate 500)
  - Subtle captions: `--text-faint: #94a3b8` (Slate 400)
- **Brand & Semantic Colors**:
  - Primary / Brand CTA: Deep Indigo (`#4f46e5`, hover `#4338ca`)
  - Success: Emerald (`#10b981`, surface `#f0fdf4`, border `#bbf7d0`)
  - Warning: Amber (`#f59e0b`, surface `#fffbeb`, border `#fde68a`)
  - Danger / Error: Rose (`#ef4444`, surface `#fef2f2`, border `#fecdd3`)
  - Info: Sky/Blue (`#3b82f6`, surface `#eff6ff`, border `#bfdbfe`)
  - Neutral: Slate (`#64748b`, surface `#f1f5f9`, border `#e2e8f0`)

---

## 3. UI Component Primitives Inventory

All primitive components in `src/components/ui/` were redesigned for consistent spacing, accessible interactive states, and clean typography:

| Component | Path | Key Enhancements |
| :--- | :--- | :--- |
| **Badge** | `Badge.tsx` | Subtle pastel backgrounds, 1px border, status indicator dot, non-wrapping whitespace, formatted snake_case text while preserving exact test string contracts. |
| **Button** | `Button.tsx` | Refined variants (`primary`, `secondary`, `outline`, `ghost`, `danger`) with solid indigo primary CTA, clean secondary borders, and flex gap direct children rendering. |
| **Card & MetricCard** | `Card.tsx`, `MetricCard.tsx` | Crisp white cards with 1px subtle borders, tabular figures for metric values, truncated headers, and clean divider spacing. |
| **Table** | `Table.tsx` | Clean neutral table design, subtle header divider, hover highlights on rows, and mono-formatted identifiers. |
| **PageHeader & SectionHeader** | `PageHeader.tsx`, `SectionHeader.tsx` | Integrated breadcrumb hierarchy, entity title, descriptive subtitles, and right-aligned action toolbars. |
| **Modal & ConfirmDialog** | `Modal.tsx`, `ConfirmDialog.tsx` | Accessible backdrop overlay, rounded-xl container, sticky header/footer with secondary Cancel and primary confirmation CTAs. |
| **Input & Select** | `Input.tsx`, `Select.tsx` | Standardized 36px control height, subtle border, indigo focus ring, clear floating/stacked labels, and error states. |
| **EmptyState** | `EmptyState.tsx` | Centered action-oriented empty states with subtle iconography and primary action buttons. |
| **LoadingState & ErrorState** | `LoadingState.tsx`, `ErrorState.tsx` | Light grey pulse skeleton loaders, friendly error summaries with collapsible technical diagnostics. |
| **Toast** | `Toast.tsx` | Ephemeral toast notifications with slide-in animations for asynchronous operation feedback. |

---

## 4. Application Shell & Navigation Layout

The application shell (`AppShell.tsx`, `Sidebar.tsx`, `Header.tsx`, `UserMenu.tsx`, `SystemStatusIndicator.tsx`) provides the responsive structure:

1. **Collapsible Sidebar (`Sidebar.tsx`)**:
   - Compact 240px (`w-60`) width on desktop (`md:pl-60`).
   - Organized into logical business categories:
     - **Overview**: Dashboard
     - **Planning**: Goals, Plans, Tasks / WBS
     - **Resources**: Resource Catalog, Availability Windows
     - **Scheduling**: Scheduling Jobs, Timetables & Schedules
     - **Operations**: Approval Inbox, Operations Analytics, Audit Trail
     - **Workflow Modeler**: BPMN Modeler, DMN Decisions, Form Builder
     - **System**: System Status & Gateway Telemetry
   - Active route highlighting using soft indigo background (`bg-indigo-50 text-indigo-700 font-semibold`) and active icon tint.
   - Smooth slide-out mobile drawer with backdrop overlay on mobile/tablet viewports.
2. **Top Application Bar (`Header.tsx`)**:
   - 56px (`h-14`) sticky top bar.
   - Contextual workspace indicator badge displaying the authenticated tenant (`tenant-pilot-001`).
   - Live system status probe pill with real-time API health telemetry.
   - User account avatar with user display name, role badges, and sign-out menu.

---

## 5. Screen-by-Screen Redesign Highlights

### 5.1 Login Page (`LoginPage.tsx`)
- **Theme**: Centered modern card with subtle border and shadow, rendered on the calm SaaS background (`--bg-app`).
- **Brand Identity**: Crisp E7 icon badge, institutional typography, and operational mission statement.
- **SSO Action**: Prominent "Sign in with Keycloak (Demo SSO)" CTA triggering standards-compliant Authorization Code + PKCE (S256).
- **Security Disclosure**: Structured mono-spaced identity metadata (Provider, Realm, Client, Flow, Demo Personas) confirming real cryptographic JWT JWKS enforcement and zero mock logins.
- **Status Indicator**: Public health probe in the top-right header confirming gateway reachability prior to authentication.

### 5.2 Executive Dashboard (`DashboardPage.tsx`)
- **Operational Summary Cards**: 4 responsive metric cards with tabular figures:
  - *Goal & Plan Progress*: Completion percentage, active plan name, task counter, and approval status badge.
  - *Schedule Status*: Publication status (`REVIEW` / `PUBLISHED`), version counter, and constraint violations.
  - *Workflow SLA*: System SLA health, active workflow instance count, and warning/breach badges.
  - *Repair Stability*: Reassigned assignment count and average movement metrics.
- **Blockers Queue**: Actionable "Identified Operational Blockers" panel with zero-state green checkmark indicator when all plan dependencies are clear.
- **Audit Stream**: "Recent Operational Event Stream" rendering live events from the immutable outbox table with relative timestamps and status badges.

### 5.3 Goals & Goal Detail (`GoalsPage.tsx`, `GoalDetailPage.tsx`)
- **Goals Catalog**: Search filter with live keyword search, high-contrast priority indicators (`HIGH`), and click-through row navigation.
- **Goal Creation Modal**: Clean modal with inputs for goal title, scope description, priority selector, target deadline datepicker, and constraint tags.
- **Goal Detail View**: Breadcrumb navigation, dual split-pane for operational scope and execution parameters, and a prominent card listing synthesized operational plans with "Inspect WBS & Critical Path" actions.

### 5.4 Plans & Work Breakdown Structure (`PlansPage.tsx`, `PlanDetailPage.tsx`)
- **Plans Registry**: Filterable table showing plan version, parent goal link, task counts, and approval state.
- **Flagship WBS Tabs**:
  - *Tasks / WBS*: Hierarchical table with task codes, descriptions, duration badges, and progress update drawers.
  - *Critical Path Analysis*: Longest-path task sequence with zero-float bottleneck highlighting.
  - *Milestones*: Key target milestones and dependency completion gates.
  - *Dependencies*: Explicit predecessor-successor graph mappings.
  - *Risks*: Operational risk assessment and mitigations.
- **Lifecycle Actions**: "Validate Plan", "Submit for Approval", and "Proceed to Scheduling" direct CTAs.

### 5.5 Resources & Availability (`ResourcesPage.tsx`, `AvailabilityPage.tsx`)
- **Resource Catalog**: Operational catalog separating human invigilators and physical examination halls, with capacity badges and qualification tags.
- **Availability Windows**: Human-readable date-time availability ranges with clean visual time slot representations.

### 5.6 Scheduling Jobs & Timetables (`SchedulingJobsPage.tsx`, `SchedulesPage.tsx`, `ScheduleDetailPage.tsx`)
- **Job Orchestration**: Solver dispatch form with real-time polling progress, status badges (`FEASIBLE`, `INFEASIBLE`, `SOLVING`), and direct "Open Timetable" navigation.
- **Schedule Registry**: Clear version lineage showing Published baselines, Review candidates, and Superseded schedules.
- **Flagship Timetable Grid (`ScheduleDetailPage.tsx`)**:
  - Two viewing modes: Timetable Matrix (resource lanes with chronological time slots) and Assignments Table.
  - Resource lanes displaying room and invigilator schedules with session codes, time windows, and date labels.
  - Visual lock indicators distinguishing fixed assignments (`LOCKED`) from flexible solver placements (`Dynamic`).
  - Concise Version 2 repair banner highlighting supersession lineage and predecessor baseline links.
  - One-click "Publish Timetable" and "Repair Schedule" modal workflows.

### 5.7 Workflow Task Inbox (`TaskInbox.tsx`)
- **Task Queue**: Real-time human task inbox with role-based filtering, task descriptions, and SLA status badges (`SLA Warning`, `SLA On Track`).
- **Action Drawer**: Right-hand action pane with task metadata, decision rationale textarea, decision dropdown, and action buttons (`Claim Task`, `Submit Approval`, `Submit Rejection`).

---

## 6. Verification & Automated Test Evidence

The redesign was verified continuously against the complete automated test suite and live backend containers:

### 6.1 Frontend Test Suite (Vitest)
```
 RUN  v3.2.7 F:/operations-engine/ui/operations-ui

 ✓ src/components/BpmnDesigner.test.tsx (3 tests)
 ✓ src/components/DmnEditor.test.tsx (3 tests)
 ✓ src/components/TaskInbox.test.tsx (3 tests)
 ✓ src/components/FormEditor.test.tsx (4 tests)
 ✓ src/api/client.test.ts (15 tests)
 ✓ src/components/OperationsDashboard.test.tsx (3 tests)
 ✓ src/test/DashboardPage.test.tsx (3 tests)
 ✓ src/test/Resources.test.tsx (3 tests)
 ✓ src/test/Goals.test.tsx (5 tests)
 ✓ src/test/AppShell.test.tsx (3 tests)
 ✓ src/test/Scheduling.test.tsx (9 tests)
 ✓ src/test/Plans.test.tsx (11 tests)
 ✓ src/test/SystemStatusIndicator.test.tsx (2 tests)
 ✓ src/test/Auth.test.tsx (10 tests)

 Test Files  14 passed (14)
      Tests  77 passed (77)
   Duration  8.89s
```

### 6.2 Code Quality & Build Checks
- **TypeScript Strict Check**: `npm run typecheck` (`tsc --noEmit`) -> **0 errors, passed with exit code 0**.
- **ESLint**: `npm run lint` (`eslint "src/**/*.{ts,tsx}"`) -> **0 warnings, 0 errors, passed with exit code 0**.
- **Production Bundle**: `npm run build` (`tsc && vite build`) -> **Built in 17.78s, passed with exit code 0**.

### 6.3 Backend Pilot Regression Suite (Pytest)
```
.venv\Scripts\python -m pytest -q tests/pilot
..................                                                       [100%]
18 passed in 3.39s
```

### 6.4 Zero Backend Diff Verification
```bash
git diff --stat -- app tests docker docker-compose.yml docker-compose.pilot.yml docker-compose.demo.yml
# (empty output - 0 files changed)
```

---

## 7. Responsive Viewport Testing Matrix

The UI was visually validated across all standard responsive breakpoints using Chrome DevTools MCP:

| Viewport | Dimensions | Layout Behavior Verified |
| :--- | :--- | :--- |
| **Desktop Ultra/Wide** | 1440 × 900 | 240px sidebar, 4-column metrics, full 2-column WBS and timetable resource lanes. |
| **Standard Laptop** | 1280 × 800 | Full sidebar, responsive grid columns, zero horizontal page scrolling. |
| **Small Desktop / Tablet Landscape** | 1024 × 768 | Metrics adapt cleanly, WBS table retains readable columns, timetable matrix wraps. |
| **Tablet Portrait** | 768 × 1024 | Metrics form 2x2 grid, navigation remains accessible, tables scroll internally if needed. |
| **Mobile Phone** | 375 × 812 | Sidebar collapses into off-canvas drawer with hamburger toggle and close `X` button; metrics stack in single column (`grid-cols-1`). |

---

## 8. Summary of Files Modified

All redesign changes were strictly contained within the frontend directory:
- `ui/operations-ui/src/index.css`: Design system tokens, utilities, and typography.
- `ui/operations-ui/src/components/ui/*`: 14 primitive UI components.
- `ui/operations-ui/src/components/layout/*`: 5 shell and navigation components.
- `ui/operations-ui/src/pages/*`: 12 feature screens and the login screen.
- `ui/operations-ui/src/components/TaskInbox.tsx`: Workflow queue and action drawer.
- `docs/demo/UI_UX_V2_DESIGN.md`: This comprehensive redesign document.

All modifications remain **uncommitted** in the working directory for human inspection.
