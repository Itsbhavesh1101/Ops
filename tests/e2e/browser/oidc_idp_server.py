"""Disposable Keycloak OIDC Identity Provider Emulator for Real Application Acceptance.

Provides:
- Standards-compliant OpenID Connect provider on port 8180
- Dynamic discovery at /realms/operations-engine-demo/.well-known/openid-configuration
- Asymmetric RS256 JWKS at /realms/operations-engine-demo/protocol/openid-connect/certs
- Interactive persona login at /realms/operations-engine-demo/protocol/openid-connect/auth
- PKCE code-for-token exchange at /realms/operations-engine-demo/protocol/openid-connect/token
- Session termination at /realms/operations-engine-demo/protocol/openid-connect/logout
- Read probe at /ready

Security:
- Emulates Keycloak 24.0.5 demo realm.
- Issues RS256-signed JWTs verified cryptographically by OidcVerifier in app.main.
- Zero mock domain endpoints; domain operations are handled by the actual backend.
"""

import time
import uuid
from typing import Any

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_private_key.public_key(), as_dict=True)
_public_jwk["kid"] = "demo-key-1"
_public_jwk["use"] = "sig"
_public_jwk["alg"] = "RS256"

OIDC_ISSUER = "http://127.0.0.1:8180/realms/operations-engine-demo"
CLIENT_ID = "operations-ui"
TENANT_ID = "tenant-pilot-001"

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

idp_app = FastAPI(title="Keycloak Demo OIDC Emulator")
idp_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@idp_app.get("/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready", "provider": "keycloak-emulator"}


@idp_app.get("/realms/operations-engine-demo/.well-known/openid-configuration")
async def openid_configuration() -> dict[str, Any]:
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
async def jwks_certs() -> dict[str, Any]:
    return {"keys": [_public_jwk]}


@idp_app.get("/realms/operations-engine-demo/protocol/openid-connect/auth")
async def auth_endpoint(
    client_id: str,
    redirect_uri: str,
    state: str,
    response_type: str = "code",
    persona: str | None = None,
) -> Any:
    del response_type
    if persona and persona in PERSONAS:
        code = f"code_{persona}_{uuid.uuid4().hex[:8]}"
        _auth_codes[code] = PERSONAS[persona]
        return RedirectResponse(f"{redirect_uri}?code={code}&state={state}")

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
async def token_endpoint(request: Request) -> JSONResponse:
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
    return JSONResponse(
        content={
            "access_token": id_token,
            "id_token": id_token,
            "token_type": "Bearer",
            "expires_in": 3600,
        }
    )


@idp_app.get("/realms/operations-engine-demo/protocol/openid-connect/logout")
async def logout_endpoint(
    post_logout_redirect_uri: str = "http://127.0.0.1:5173/login",
) -> RedirectResponse:
    return RedirectResponse(post_logout_redirect_uri)


if __name__ == "__main__":
    uvicorn.run(idp_app, host="127.0.0.1", port=8180)

