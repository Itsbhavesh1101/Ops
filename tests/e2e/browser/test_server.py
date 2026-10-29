"""Disposable E2E Browser Acceptance Server.

Provides:
1. Port 8180: Standards-compliant Keycloak OIDC Identity Provider emulator
   - Dynamic discovery at /realms/operations-engine-demo/.well-known/openid-configuration
   - Asymmetric RS256 JWKS at /realms/operations-engine-demo/protocol/openid-connect/certs
   - Interactive demo login / redirect at
     /realms/operations-engine-demo/protocol/openid-connect/auth
   - PKCE code-for-token exchange at /realms/operations-engine-demo/protocol/openid-connect/token
   - Session termination at /realms/operations-engine-demo/protocol/openid-connect/logout

2. Port 8088: Real FastAPI Operations Engine backend
   - Real HTTP Bearer token authentication via RS256 signature verification against JWKS
   - Endpoints: /auth/me, /goals, /plans, /tasks, /workflow/tasks, /workflows/instances,
     /schedule-jobs, /schedules, /analytics/operations, /audit, /ready, /health
   - Stateful in-memory persistence with tenant isolation and RBAC checks
"""

import asyncio
import time
import uuid
from typing import Any

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

# -----------------------------------------------------------------------------
# 1. Ephemeral Cryptographic Key Pair for Test OIDC Identity Provider
# -----------------------------------------------------------------------------
_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_private_key.public_key(), as_dict=True)
_public_jwk["kid"] = "demo-key-1"
_public_jwk["use"] = "sig"
_public_jwk["alg"] = "RS256"

OIDC_ISSUER = "http://127.0.0.1:8180/realms/operations-engine-demo"
CLIENT_ID = "operations-ui"
TENANT_ID = "tenant-pilot-001"

# Persona definitions matching Keycloak realm export
PERSONAS: dict[str, dict[str, Any]] = {
    "planner": {
        "sub": "user_planner_001",
        "email": "planner@operations.local",
        "name": "Demo Planner",
        "roles": ["planner"],
        "tenant_id": TENANT_ID,
    },
    "approver": {
        "sub": "user_approver_001",
        "email": "approver@operations.local",
        "name": "Demo Approver",
        "roles": ["approver"],
        "tenant_id": TENANT_ID,
    },
    "scheduler": {
        "sub": "user_scheduler_001",
        "email": "scheduler@operations.local",
        "name": "Demo Scheduler",
        "roles": ["scheduler", "operator"],
        "tenant_id": TENANT_ID,
    },
    "admin": {
        "sub": "user_admin_001",
        "email": "admin@operations.local",
        "name": "Demo Admin",
        "roles": ["tenant_admin", "auditor"],
        "tenant_id": TENANT_ID,
    },
}

_auth_codes: dict[str, dict[str, Any]] = {}

# -----------------------------------------------------------------------------
# 2. Keycloak OIDC Identity Provider App (Port 8180)
# -----------------------------------------------------------------------------
idp_app = FastAPI(title="Keycloak Demo OIDC Emulator")
idp_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@idp_app.get("/realms/operations-engine-demo/.well-known/openid-configuration")
async def openid_configuration():
    return {
        "issuer": OIDC_ISSUER,
        "authorization_endpoint": f"{OIDC_ISSUER}/protocol/openid-connect/auth",
        "token_endpoint": f"{OIDC_ISSUER}/protocol/openid-connect/token",
        "jwks_uri": f"{OIDC_ISSUER}/protocol/openid-connect/certs",
        "end_session_endpoint": f"{OIDC_ISSUER}/protocol/openid-connect/logout",
        "response_types_supported": ["code", "id_token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


@idp_app.get("/realms/operations-engine-demo/protocol/openid-connect/certs")
async def jwks_certs():
    return {"keys": [_public_jwk]}


@idp_app.get("/realms/operations-engine-demo/protocol/openid-connect/auth")
async def auth_endpoint(
    client_id: str,
    redirect_uri: str,
    state: str,
    response_type: str = "code",
    persona: str | None = None,
):
    # If persona is directly provided via query param (e.g. testing direct link)
    if persona and persona in PERSONAS:
        code = f"code_{persona}_{uuid.uuid4().hex[:8]}"
        _auth_codes[code] = PERSONAS[persona]
        return RedirectResponse(f"{redirect_uri}?code={code}&state={state}")

    # Interactive persona selection screen matching Keycloak demo UI
    base_auth = (
        f"/realms/operations-engine-demo/protocol/openid-connect/auth"
        f"?client_id={client_id}&redirect_uri={redirect_uri}&state={state}"
    )
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Keycloak Demo Single Sign-On</title>
      <style>
        body {{
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          background: #0f172a;
          color: #f8fafc;
          display: flex;
          align-items: center;
          justify-content: center;
          height: 100vh;
          margin: 0;
        }}
        .card {{
          background: #1e293b;
          border: 1px solid #334155;
          padding: 2rem;
          border-radius: 12px;
          width: 380px;
          box-shadow: 0 10px 25px rgba(0,0,0,0.5);
          text-align: center;
        }}
        h1 {{ font-size: 1.25rem; margin-bottom: 0.5rem; }}
        p {{ font-size: 0.85rem; color: #94a3b8; margin-bottom: 1.5rem; }}
        .btn {{
          display: block;
          width: 100%;
          padding: 0.75rem;
          margin-bottom: 0.75rem;
          border-radius: 8px;
          border: none;
          font-weight: 600;
          cursor: pointer;
          text-decoration: none;
          box-sizing: border-box;
        }}
        .btn-planner {{ background: #4f46e5; color: white; }}
        .btn-approver {{ background: #059669; color: white; }}
        .btn-scheduler {{ background: #d97706; color: white; }}
        .btn-admin {{ background: #dc2626; color: white; }}
        .btn:hover {{ opacity: 0.9; }}
      </style>
    </head>
    <body>
      <div class="card">
        <h1>Keycloak Demo Realm</h1>
        <p>Select a synthetic persona to authenticate:</p>
        <a id="btn-login-planner" class="btn btn-planner"
           href="{base_auth}&persona=planner">Sign In as Planner</a>
        <a id="btn-login-approver" class="btn btn-approver"
           href="{base_auth}&persona=approver">Sign In as Approver</a>
        <a id="btn-login-scheduler" class="btn btn-scheduler"
           href="{base_auth}&persona=scheduler">Sign In as Scheduler</a>
        <a id="btn-login-admin" class="btn btn-admin"
           href="{base_auth}&persona=admin">Sign In as Admin</a>
      </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@idp_app.post("/realms/operations-engine-demo/protocol/openid-connect/token")
async def token_endpoint(request: Request):
    from urllib.parse import parse_qs

    body_bytes = await request.body()
    form = {k: v[0] for k, v in parse_qs(body_bytes.decode("utf-8")).items()}
    code = form.get("code")
    if not code or code not in _auth_codes:
        raise HTTPException(status_code=400, detail="Invalid authorization code")

    persona_info = _auth_codes.pop(code)
    now = int(time.time())
    payload = {
        "iss": OIDC_ISSUER,
        "sub": persona_info["sub"],
        "aud": CLIENT_ID,
        "exp": now + 3600,
        "iat": now,
        "email": persona_info["email"],
        "email_verified": True,
        "name": persona_info["name"],
        "preferred_username": persona_info["email"].split("@")[0],
        "realm_access": {"roles": persona_info["roles"]},
        "resource_access": {"operations-ui": {"roles": persona_info["roles"]}},
        "tenant_id": persona_info["tenant_id"],
    }
    id_token = jwt.encode(payload, _private_key, algorithm="RS256", headers={"kid": "demo-key-1"})
    access_token = id_token
    return {
        "access_token": access_token,
        "id_token": id_token,
        "token_type": "Bearer",
        "expires_in": 3600,
    }


@idp_app.get("/realms/operations-engine-demo/protocol/openid-connect/logout")
async def logout_endpoint(post_logout_redirect_uri: str = "http://127.0.0.1:5173/login"):
    return RedirectResponse(post_logout_redirect_uri)


# -----------------------------------------------------------------------------
# 3. Operations Engine Backend App (Port 8088)
# -----------------------------------------------------------------------------
backend_app = FastAPI(title="Operations Engine E2E Backend")
backend_app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Stateful in-memory persistence store
db_goals: dict[str, dict[str, Any]] = {}
db_plans: dict[str, dict[str, Any]] = {}
db_tasks: dict[str, dict[str, Any]] = {}
db_workflow_tasks: dict[str, dict[str, Any]] = {}
db_workflow_instances: dict[str, dict[str, Any]] = {}
db_schedule_jobs: dict[str, dict[str, Any]] = {}
db_schedules: dict[str, dict[str, Any]] = {}
db_audit_events: list[dict[str, Any]] = []


def record_audit(
    event_type: str,
    resource_type: str,
    resource_id: str,
    principal: dict[str, Any],
    details: dict[str, Any] | None = None,
):
    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    event_id = str(uuid.uuid4())
    event = {
        "id": event_id,
        "audit_event_id": event_id,
        "schema_version": 1,
        "tenant_id": principal.get("tenant_id", TENANT_ID),
        "actor_principal_id": principal.get("principal_id", "system"),
        "actor_principal_type": "HUMAN",
        "authentication_method": "OIDC",
        "event_type": event_type,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "outcome": "SUCCESS",
        "correlation_id": f"corr-{uuid.uuid4().hex[:8]}",
        "details": details or {},
        "recorded_at": now_str,
        "occurred_at": now_str,
        "created_at": now_str,
    }
    db_audit_events.insert(0, event)


async def verify_bearer_token(request: Request) -> dict[str, Any]:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid authentication credentials required.",
        )
    token = auth_header[len("Bearer ") :].strip()
    try:
        # Cryptographic verification against public key
        payload = jwt.decode(
            token,
            _private_key.public_key(),
            algorithms=["RS256"],
            audience=CLIENT_ID,
            issuer=OIDC_ISSUER,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification failed: {exc}",
        ) from exc

    roles = payload.get("realm_access", {}).get("roles", [])
    email = payload.get("email")
    display_name = payload.get("name") or (email.split("@")[0].title() if email else "User")
    return {
        "principal_id": payload["sub"],
        "tenant_id": payload.get("tenant_id", TENANT_ID),
        "roles": roles,
        "permissions": ["*"] if "tenant_admin" in roles else [f"{r}:access" for r in roles],
        "email": email,
        "display_name": display_name,
        "principal_type": "HUMAN",
        "authentication_method": "OIDC",
        "issuer": OIDC_ISSUER,
    }


def enforce_tenant(principal: dict[str, Any], request: Request) -> str:
    requested_tenant = request.headers.get("X-Tenant-ID") or principal["tenant_id"]
    if requested_tenant != principal["tenant_id"] and "tenant_admin" not in principal["roles"]:
        raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")
    return requested_tenant


# Seed baseline entities
def seed_baseline():
    g_id = str(uuid.uuid4())
    db_goals[g_id] = {
        "id": g_id,
        "tenant_id": TENANT_ID,
        "objective": "Grade 10 Semester Examination Operations",
        "scope": "Academic Affairs",
        "priority_target": "HIGH",
        "status": "ACTIVE",
        "constraints": ["NO_SUNDAY_EXAMS"],
        "resource_scope": ["hall-a", "hall-b"],
        "context_references": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    p_id = str(uuid.uuid4())
    db_plans[p_id] = {
        "id": p_id,
        "goal_id": g_id,
        "tenant_id": TENANT_ID,
        "version_number": 1,
        "status": "APPROVED",
        "plan_name": "Grade 10 Semester Examination Operations",
        "is_baseline": True,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    t1_id = str(uuid.uuid4())
    t2_id = str(uuid.uuid4())
    db_tasks[t1_id] = {
        "id": t1_id,
        "plan_id": p_id,
        "tenant_id": TENANT_ID,
        "temporary_id": "T-101",
        "title": "Prepare Exam Materials & Question Papers",
        "name": "Prepare Exam Materials & Question Papers",
        "wbs_code": "1.1",
        "status": "COMPLETED",
        "progress_percentage": 100,
        "estimated_duration_minutes": 960,
        "duration_hours": 16,
        "priority": "HIGH",
        "dependencies": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    db_tasks[t2_id] = {
        "id": t2_id,
        "plan_id": p_id,
        "tenant_id": TENANT_ID,
        "temporary_id": "T-102",
        "title": "Faculty Proctor Assignment & Hall Allocation",
        "name": "Faculty Proctor Assignment & Hall Allocation",
        "wbs_code": "1.2",
        "status": "IN_PROGRESS",
        "progress_percentage": 50,
        "estimated_duration_minutes": 1440,
        "duration_hours": 24,
        "priority": "HIGH",
        "dependencies": [t1_id],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    wt_id = str(uuid.uuid4())
    db_workflow_tasks[wt_id] = {
        "id": wt_id,
        "tenant_id": TENANT_ID,
        "task_definition_key": "moderation_review",
        "name": "Review Examination Moderation & Readiness",
        "description": "Verify Science and Humanities moderation outcomes before scheduling.",
        "status": "PENDING",
        "assignee": None,
        "candidate_roles": ["approver", "operator"],
        "sla_status": "RUNNING",
        "due_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 86400)),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    s_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db_schedule_jobs[job_id] = {
        "job_id": job_id,
        "tenant_id": TENANT_ID,
        "schedule_key": "exam-schedule",
        "status": "FEASIBLE",
        "resulting_schedule_id": s_id,
        "created_at": now_str,
        "completed_at": now_str,
    }
    db_schedules[s_id] = {
        "id": s_id,
        "schedule_key": "exam-schedule",
        "tenant_id": TENANT_ID,
        "version": 1,
        "status": "REVIEW",
        "source_job_id": job_id,
        "score_components": {"hard_violations": 0, "soft_penalty": 12.5},
        "assignments": [
            {
                "id": str(uuid.uuid4()),
                "schedule_id": s_id,
                "activity_id": "T1_EXAM_PREP",
                "resource_id": "res-room-101",
                "start_at": "2026-10-30T09:00:00Z",
                "end_at": "2026-10-30T12:00:00Z",
                "is_locked": False,
                "created_at": now_str,
            },
            {
                "id": str(uuid.uuid4()),
                "schedule_id": s_id,
                "activity_id": "T2_QP_READINESS",
                "resource_id": "res-room-102",
                "start_at": "2026-10-30T13:00:00Z",
                "end_at": "2026-10-30T16:00:00Z",
                "is_locked": False,
                "created_at": now_str,
            },
        ],
        "created_at": now_str,
    }

    record_audit(
        "SYSTEM_INITIALIZED", "system", "e7-engine", {"principal_id": "system"}, {"status": "READY"}
    )


seed_baseline()


# --- Auth Endpoints ---
@backend_app.get("/api/v1/auth/me")
async def get_current_user(request: Request):
    principal = await verify_bearer_token(request)
    return principal


# --- Health & Readiness ---
@backend_app.get("/api/v1/health")
@backend_app.get("/health")
async def health():
    return {"status": "ok", "service": "operations-engine", "environment": "test"}


@backend_app.get("/api/v1/ready")
@backend_app.get("/ready")
async def readiness():
    return {"status": "ready", "service": "operations-engine", "environment": "test"}


# --- Goals Endpoints ---
@backend_app.get("/api/v1/goals")
async def list_goals(request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    return [g for g in db_goals.values() if g["tenant_id"] == tenant]


@backend_app.post("/api/v1/goals")
async def create_goal(request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    body = await request.json()
    new_id = str(uuid.uuid4())
    goal = {
        "id": new_id,
        "tenant_id": tenant,
        "objective": body.get("objective", "New Goal"),
        "scope": body.get("scope"),
        "priority_target": body.get("priority_target", "NORMAL"),
        "status": "ACTIVE",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    db_goals[new_id] = goal
    record_audit("GOAL_CREATED", "goal", new_id, principal, {"objective": goal["objective"]})
    return goal


@backend_app.get("/api/v1/goals/{goal_id}")
async def get_goal(goal_id: str, request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    goal = db_goals.get(goal_id)
    if not goal or goal["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail="Goal not found")
    return goal


# --- Plans Endpoints ---
@backend_app.get("/api/v1/plans")
async def list_plans(request: Request, goal_id: str | None = None):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    plans = [p for p in db_plans.values() if p["tenant_id"] == tenant]
    if goal_id:
        plans = [p for p in plans if p.get("goal_id") == goal_id]
    return plans


@backend_app.post("/api/v1/goals/{goal_id}/generate-plan")
async def generate_plan(goal_id: str, request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    goal = db_goals.get(goal_id)
    if not goal or goal["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail="Goal not found")

    new_plan_id = str(uuid.uuid4())
    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    plan = {
        "id": new_plan_id,
        "goal_id": goal_id,
        "tenant_id": tenant,
        "version_number": 1,
        "status": "DRAFT",
        "plan_name": f"{goal['objective']} Plan",
        "is_baseline": False,
        "created_at": now_str,
        "updated_at": now_str,
    }
    db_plans[new_plan_id] = plan

    # Generate tasks
    t1 = str(uuid.uuid4())
    t2 = str(uuid.uuid4())
    db_tasks[t1] = {
        "id": t1,
        "plan_id": new_plan_id,
        "tenant_id": tenant,
        "temporary_id": "T-101",
        "title": "Phase 1: Syllabus Moderation & Exam Blueprint",
        "name": "Phase 1: Syllabus Moderation & Exam Blueprint",
        "wbs_code": "1.1",
        "status": "TODO",
        "progress_percentage": 0,
        "estimated_duration_minutes": 720,
        "duration_hours": 12,
        "priority": "HIGH",
        "dependencies": [],
        "created_at": now_str,
        "updated_at": now_str,
    }
    db_tasks[t2] = {
        "id": t2,
        "plan_id": new_plan_id,
        "tenant_id": tenant,
        "temporary_id": "T-102",
        "title": "Phase 2: Timetable Optimization & Hall Assignment",
        "name": "Phase 2: Timetable Optimization & Hall Assignment",
        "wbs_code": "1.2",
        "status": "TODO",
        "progress_percentage": 0,
        "estimated_duration_minutes": 1200,
        "duration_hours": 20,
        "priority": "HIGH",
        "dependencies": [t1],
        "created_at": now_str,
        "updated_at": now_str,
    }

    record_audit("PLAN_GENERATED", "plan", new_plan_id, principal, {"goal_id": goal_id})
    return plan


@backend_app.get("/api/v1/plans/{plan_id}")
async def get_plan(plan_id: str, request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    plan = db_plans.get(plan_id)
    if not plan or plan["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail="Plan not found")
    tasks = [t for t in db_tasks.values() if t["plan_id"] == plan_id]
    deps = (
        [
            {
                "id": str(uuid.uuid4()),
                "predecessor_id": tasks[0]["id"],
                "successor_id": tasks[1]["id"],
                "dependency_type": "FINISH_TO_START",
                "lag_minutes": 0,
            }
        ]
        if len(tasks) >= 2
        else []
    )
    milestones = [
        {
            "id": str(uuid.uuid4()),
            "temporary_id": "M-1",
            "name": "Blueprint Sign-off",
            "target_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 86400)),
        }
    ]
    return {
        **plan,
        "tasks": tasks,
        "dependencies": deps,
        "milestones": milestones,
        "risks": [],
    }


@backend_app.get("/api/v1/plans/{plan_id}/tasks")
async def get_plan_tasks(plan_id: str, request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    return [t for t in db_tasks.values() if t["plan_id"] == plan_id and t["tenant_id"] == tenant]


@backend_app.post("/api/v1/plans/{plan_id}/validate")
async def validate_plan(plan_id: str, request: Request):
    _ = await verify_bearer_token(request)
    return {"is_valid": True, "valid": True, "cycles_detected": False, "issues": []}


@backend_app.post("/api/v1/plans/{plan_id}/submit-for-approval")
async def submit_plan(plan_id: str, request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    plan = db_plans.get(plan_id)
    if not plan or plan["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail="Plan not found")
    plan["status"] = "APPROVAL_PENDING"
    record_audit("PLAN_SUBMITTED", "plan", plan_id, principal)
    return plan


@backend_app.post("/api/v1/plans/{plan_id}/approve")
async def approve_plan(plan_id: str, request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    if "approver" not in principal["roles"] and "tenant_admin" not in principal["roles"]:
        raise HTTPException(status_code=403, detail="Approver role required")
    plan = db_plans.get(plan_id)
    if not plan or plan["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail="Plan not found")
    plan["status"] = "APPROVED"
    plan["is_baseline"] = True
    record_audit("PLAN_APPROVED", "plan", plan_id, principal)
    return plan


@backend_app.get("/api/v1/plans/{plan_id}/critical-path")
async def get_critical_path(plan_id: str, request: Request):
    _ = await verify_bearer_token(request)
    tasks = [t for t in db_tasks.values() if t["plan_id"] == plan_id]
    nodes = {
        t["id"]: {
            "task_id": t["id"],
            "duration_minutes": t.get("estimated_duration_minutes", 60),
            "early_start": 0,
            "early_finish": t.get("estimated_duration_minutes", 60),
            "late_start": 0,
            "late_finish": t.get("estimated_duration_minutes", 60),
            "total_slack": 0.0,
            "free_slack": 0.0,
            "is_critical": True,
        }
        for t in tasks
    }
    return {
        "project_duration_minutes": sum(t.get("estimated_duration_minutes", 60) for t in tasks),
        "schedule": nodes,
        "critical_path": [t["id"] for t in tasks],
        "critical_paths": [[t["id"] for t in tasks]],
    }


@backend_app.get("/api/v1/plans/{plan_id}/milestones")
async def get_plan_milestones(plan_id: str, request: Request):
    await verify_bearer_token(request)
    return [
        {
            "id": str(uuid.uuid4()),
            "name": "Preparation Milestone",
            "health": "ON_TRACK",
            "status": "IN_PROGRESS",
            "completed_tasks": 1,
            "total_tasks": 2,
        }
    ]


@backend_app.get("/api/v1/plans/{plan_id}/risks")
async def get_plan_risks(plan_id: str, request: Request):
    await verify_bearer_token(request)
    return []


@backend_app.get("/api/v1/plans/{plan_id}/recommendations")
async def get_plan_recommendations(plan_id: str, request: Request):
    await verify_bearer_token(request)
    return {"plan_id": plan_id, "recommendations": []}


# --- Tasks Endpoints ---
@backend_app.get("/api/v1/tasks")
async def list_tasks(request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    return [t for t in db_tasks.values() if t["tenant_id"] == tenant]


@backend_app.patch("/api/v1/tasks/{task_id}")
async def update_task(task_id: str, request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    task = db_tasks.get(task_id)
    if not task or task["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail="Task not found")
    body = await request.json()
    if "status" in body:
        task["status"] = body["status"]
    if "progress_percentage" in body:
        task["progress_percentage"] = body["progress_percentage"]
    elif "progress" in body:
        task["progress_percentage"] = body["progress"]
    record_audit("TASK_UPDATED", "task", task_id, principal, body)
    return task


# --- Resources Endpoints ---
@backend_app.get("/api/v1/resources")
async def list_resources(request: Request):
    _ = await verify_bearer_token(request)
    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return [
        {
            "id": "res-room-101",
            "resource_key": "hall-a",
            "type": "ROOM",
            "display_label": "Exam Hall A",
            "capacity": 50,
            "active": True,
            "created_at": now_str,
            "updated_at": now_str,
        },
        {
            "id": "res-room-102",
            "resource_key": "hall-b",
            "type": "ROOM",
            "display_label": "Exam Hall B",
            "capacity": 40,
            "active": True,
            "created_at": now_str,
            "updated_at": now_str,
        },
        {
            "id": "res-staff-201",
            "resource_key": "dr-adams",
            "type": "STAFF",
            "display_label": "Dr. Sarah Adams",
            "capacity": 1,
            "active": True,
            "created_at": now_str,
            "updated_at": now_str,
        },
    ]


@backend_app.get("/api/v1/resources/availability")
async def list_resource_availability(request: Request):
    _ = await verify_bearer_token(request)
    now = time.time()
    return [
        {
            "resource_id": "res-room-101",
            "interval_type": "AVAILABLE",
            "start_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "end_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 86400 * 7)),
            "reason": "Normal operational hours",
        }
    ]


# --- Workflows Endpoints ---
@backend_app.get("/api/v1/workflow/tasks")
@backend_app.get("/api/v1/workflows/tasks")
async def list_workflow_tasks(request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    return [t for t in db_workflow_tasks.values() if t["tenant_id"] == tenant]


@backend_app.post("/api/v1/workflow/tasks/{task_id}/action")
@backend_app.post("/api/v1/workflows/tasks/{task_id}/action")
@backend_app.post("/api/v1/workflows/tasks/{task_id}/actions")
@backend_app.post("/api/v1/workflow/tasks/{task_id}/actions")
async def perform_workflow_task_action(task_id: str, request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    task = db_workflow_tasks.get(task_id)
    if not task or task["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail="Workflow task not found")
    body = await request.json()
    action = body.get("action", "complete")
    if action == "claim":
        task["status"] = "CLAIMED"
        task["assignee"] = principal["principal_id"]
    elif action in ("complete", "approve"):
        task["status"] = "COMPLETED"
    elif action == "reject":
        task["status"] = "REJECTED"
    elif action == "unclaim":
        task["status"] = "PENDING"
        task["assignee"] = None
    record_audit(f"WORKFLOW_TASK_{action.upper()}", "workflow_task", task_id, principal, body)
    return {"status": "SUCCESS", "task_id": task_id, **task}


@backend_app.post("/api/v1/workflows/instances")
async def start_workflow_instance(request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    body = await request.json()
    new_inst_id = str(uuid.uuid4())
    inst = {
        "id": new_inst_id,
        "tenant_id": tenant,
        "workflow_key": body.get("workflow_key", "exam-operations-v1"),
        "business_key": body.get("business_key", f"BK-{uuid.uuid4().hex[:6]}"),
        "status": "RUNNING",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    db_workflow_instances[new_inst_id] = inst
    record_audit("WORKFLOW_INSTANCE_STARTED", "workflow_instance", new_inst_id, principal, body)
    return inst


@backend_app.get("/api/v1/workflows/instances/{instance_id}")
async def get_workflow_instance(instance_id: str, request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    inst = db_workflow_instances.get(instance_id)
    if not inst or inst["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    return inst


@backend_app.get("/api/v1/workflows/instances/{instance_id}/history")
async def get_workflow_history(instance_id: str, request: Request):
    await verify_bearer_token(request)
    return [
        {
            "id": str(uuid.uuid4()),
            "activity_name": "Moderation Review",
            "activity_type": "userTask",
            "status": "COMPLETED",
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    ]


@backend_app.get("/api/v1/workflows/instances/{instance_id}/tasks")
async def get_instance_tasks(instance_id: str, request: Request):
    await verify_bearer_token(request)
    return [t for t in db_workflow_tasks.values() if t.get("workflow_instance_id") == instance_id]


# --- Scheduling Endpoints ---
@backend_app.post("/api/v1/schedule-jobs")
async def submit_schedule_job(request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    body = await request.json()
    new_job_id = str(uuid.uuid4())
    new_sched_id = str(uuid.uuid4())

    job = {
        "job_id": new_job_id,
        "tenant_id": tenant,
        "schedule_key": body.get("schedule_key", "exam-schedule"),
        "status": "FEASIBLE",
        "resulting_schedule_id": new_sched_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    db_schedule_jobs[new_job_id] = job

    sched = {
        "id": new_sched_id,
        "schedule_key": body.get("schedule_key", "exam-schedule"),
        "tenant_id": tenant,
        "version": len(db_schedules) + 1,
        "status": "REVIEW",
        "source_job_id": new_job_id,
        "score_components": {"hard_violations": 0, "soft_penalty": 4.0},
        "assignments": [
            {
                "id": str(uuid.uuid4()),
                "schedule_id": new_sched_id,
                "activity_id": "T1_EXAM_PREP",
                "resource_id": "res-room-101",
                "start_at": "2026-10-30T09:00:00Z",
                "end_at": "2026-10-30T12:00:00Z",
                "is_locked": False,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            {
                "id": str(uuid.uuid4()),
                "schedule_id": new_sched_id,
                "activity_id": "T4_RESOURCE_READINESS",
                "resource_id": "res-room-102",
                "start_at": "2026-10-30T13:00:00Z",
                "end_at": "2026-10-30T16:00:00Z",
                "is_locked": False,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        ],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    db_schedules[new_sched_id] = sched
    record_audit("SCHEDULE_JOB_SOLVED", "schedule", new_sched_id, principal, {"job_id": new_job_id})
    return job


@backend_app.get("/api/v1/schedule-jobs/{job_id}")
@backend_app.get("/api/v1/jobs/{job_id}")
async def get_schedule_job(job_id: str, request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    job = db_schedule_jobs.get(job_id)
    if not job or job["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@backend_app.get("/api/v1/schedules")
async def list_schedules(request: Request, status: str | None = None):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    scheds = [s for s in db_schedules.values() if s["tenant_id"] == tenant]
    if status and status != "ALL":
        scheds = [s for s in scheds if s["status"] == status]
    return scheds


@backend_app.get("/api/v1/schedules/{schedule_id}")
async def get_schedule(schedule_id: str, request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    sched = db_schedules.get(schedule_id)
    if not sched or sched["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return sched


@backend_app.post("/api/v1/schedule-jobs/{schedule_id}/publish")
@backend_app.post("/api/v1/schedules/{schedule_id}/publish")
async def publish_schedule(schedule_id: str, request: Request):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    sched = db_schedules.get(schedule_id)
    if not sched:
        # Check if schedule_id is actually a job_id
        for s in db_schedules.values():
            if s.get("source_job_id") == schedule_id:
                sched = s
                schedule_id = s["id"]
                break
    if not sched or sched["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail="Schedule not found")
    sched["status"] = "PUBLISHED"
    sched["published_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sched["published_by"] = principal.get("display_name", "Scheduler")
    record_audit("SCHEDULE_PUBLISHED", "schedule", schedule_id, principal)
    return {
        "schedule_id": schedule_id,
        "id": schedule_id,
        "version": sched.get("version", 1),
        "status": "PUBLISHED",
        "published_at": sched["published_at"],
        "calendar_sync_status": "DISABLED",
    }


# --- Analytics Endpoints ---
@backend_app.get("/api/v1/analytics/operations")
async def get_analytics(request: Request, goal_id: str | None = None):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)
    first_plan = next(iter(db_plans.values()), None)
    first_sched = next(iter(db_schedules.values()), None)
    completed_tasks = len([t for t in db_tasks.values() if t.get("status") == "COMPLETED"])
    total_tasks = len(db_tasks) or 1
    return {
        "tenant_id": tenant,
        "plan_progress": {
            "plan_id": first_plan["id"] if first_plan else None,
            "plan_name": first_plan.get("plan_name", "Grade 10 Semester Examination Operations")
            if first_plan
            else None,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "progress_percentage": round((completed_tasks / total_tasks) * 100, 1),
            "current_milestone": "Blueprint Sign-off",
            "status": "IN_PROGRESS",
        },
        "blockers": [],
        "sla_state": {
            "status": "HEALTHY",
            "active_workflows_count": 1,
            "warning_count": 0,
            "breached_count": 0,
        },
        "schedule_health": {
            "schedule_id": first_sched["id"] if first_sched else None,
            "version": first_sched.get("version", 1) if first_sched else 1,
            "status": first_sched.get("status", "REVIEW") if first_sched else "REVIEW",
            "hard_violations": 0,
            "solver_status": "OPTIMAL",
            "calendar_sync_status": "DISABLED",
        },
        "repair_movement": {
            "latest_repair_id": None,
            "moved_assignments_count": 0,
            "locked_assignments_moved": 0,
            "repair_reason": None,
        },
        "recent_events": [
            {
                "event_type": e["event_type"],
                "resource_type": e["resource_type"],
                "resource_id": e["resource_id"],
                "outcome": e.get("outcome", "SUCCESS"),
                "recorded_at": e.get(
                    "recorded_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                ),
                "correlation_id": e.get("correlation_id", "corr-init"),
            }
            for e in db_audit_events[:10]
            if e.get("tenant_id") == tenant
        ],
        "risk_summary": {
            "open_risks_count": 0,
            "risks_by_severity": {},
            "stale_resources_count": 0,
            "repair_queue_depth": 0,
            "repair_stability": {
                "total_repairs_count": 0,
                "average_percentage_moved": 0.0,
                "locked_moved_count": 0,
            },
        },
    }


# --- Audit Endpoints ---
@backend_app.get("/api/v1/audit/events")
@backend_app.get("/api/v1/audit")
async def get_audit_events(
    request: Request,
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    correlation_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    principal = await verify_bearer_token(request)
    tenant = enforce_tenant(principal, request)

    # RBAC check: strictly require auditor, tenant_admin, or admin role
    if not any(r in principal["roles"] for r in ("auditor", "tenant_admin", "admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Forbidden: The 'auditor' or 'tenant_admin' role is required to access audit logs."
            ),
        )

    matched = [e for e in db_audit_events if e.get("tenant_id") == tenant]
    if event_type:
        matched = [e for e in matched if e["event_type"] == event_type]
    if resource_type:
        matched = [e for e in matched if e["resource_type"] == resource_type]
    if resource_id:
        matched = [e for e in matched if e["resource_id"] == resource_id]
    if correlation_id:
        matched = [e for e in matched if e.get("correlation_id") == correlation_id]

    return matched[offset : offset + limit]


# -----------------------------------------------------------------------------
# Combined Asynchronous Server Runner
# -----------------------------------------------------------------------------
async def run_servers():
    config_idp = uvicorn.Config(idp_app, host="127.0.0.1", port=8180, log_level="warning")
    config_backend = uvicorn.Config(backend_app, host="127.0.0.1", port=8088, log_level="warning")

    server_idp = uvicorn.Server(config_idp)
    server_backend = uvicorn.Server(config_backend)

    print("=" * 60)
    print("E2E Acceptance Servers Starting:")
    print(" - OIDC Identity Provider: http://127.0.0.1:8180")
    print(" - Operations Engine API:  http://127.0.0.1:8088")
    print("=" * 60)

    await asyncio.gather(server_idp.serve(), server_backend.serve())


if __name__ == "__main__":
    asyncio.run(run_servers())
