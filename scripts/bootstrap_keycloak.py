"""Reproducible local Keycloak demo user credentials bootstrap script.

Configures credentials for provisioned demo users ('planner', 'approver',
'scheduler', 'admin') in the 'operations-engine-demo' realm using Keycloak's
administrative REST API.

Reads password from the KEYCLOAK_DEMO_PASSWORD environment variable (e.g. from
local gitignored .env). Never commits plaintext passwords to version control.
"""

import os
import sys
import time

import httpx
from dotenv import load_dotenv

# Load local environment configuration
load_dotenv()

KEYCLOAK_BASE_URL = os.getenv("KEYCLOAK_BASE_URL", "http://127.0.0.1:8180")
KEYCLOAK_ADMIN = os.getenv("KEYCLOAK_ADMIN", "admin")
KEYCLOAK_ADMIN_PASSWORD = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "operations-engine-demo")
DEMO_PASSWORD = os.getenv("KEYCLOAK_DEMO_PASSWORD") or os.getenv("DEMO_USER_PASSWORD")
if not DEMO_PASSWORD:
    raise RuntimeError(
        "KEYCLOAK_DEMO_PASSWORD or DEMO_USER_PASSWORD environment variable "
        "is required to bootstrap demo credentials."
    )

DEMO_USERS = ["planner", "approver", "scheduler", "admin"]


def wait_for_keycloak(client: httpx.Client, max_retries: int = 30, delay: float = 2.0) -> bool:
    """Wait until Keycloak is ready to serve requests."""
    discovery_url = f"{KEYCLOAK_BASE_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
    print(f"Waiting for Keycloak at {KEYCLOAK_BASE_URL}...")
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.get(discovery_url, timeout=3.0)
            if resp.status_code == 200:
                print(f"Keycloak realm '{KEYCLOAK_REALM}' is ready (attempt {attempt}).")
                return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def get_admin_token(client: httpx.Client) -> str:
    """Authenticate as Keycloak admin and retrieve bearer access token."""
    token_url = f"{KEYCLOAK_BASE_URL}/realms/master/protocol/openid-connect/token"
    data = {
        "client_id": "admin-cli",
        "grant_type": "password",
        "username": KEYCLOAK_ADMIN,
        "password": KEYCLOAK_ADMIN_PASSWORD,
    }
    resp = client.post(token_url, data=data, timeout=10.0)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to obtain Keycloak admin token: HTTP {resp.status_code} - {resp.text}"
        )
    return resp.json()["access_token"]


def set_user_password(client: httpx.Client, admin_token: str, username: str, password: str) -> None:
    """Reset password for a specific demo user in the demo realm."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    users_url = f"{KEYCLOAK_BASE_URL}/admin/realms/{KEYCLOAK_REALM}/users"
    resp = client.get(users_url, params={"username": username, "exact": "true"}, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to lookup user '{username}': HTTP {resp.status_code} - {resp.text}"
        )
    users = resp.json()
    if not users:
        raise RuntimeError(f"User '{username}' not found in realm '{KEYCLOAK_REALM}'")
    user_id = users[0]["id"]

    reset_url = f"{KEYCLOAK_BASE_URL}/admin/realms/{KEYCLOAK_REALM}/users/{user_id}/reset-password"
    resp = client.put(
        reset_url,
        json={"type": "password", "value": password, "temporary": False},
        headers=headers,
    )
    if resp.status_code not in (200, 204):
        raise RuntimeError(
            f"Failed to set password for user '{username}': HTTP {resp.status_code} - {resp.text}"
        )
    print(f"Successfully configured password for demo user '{username}'.")


def main() -> int:
    with httpx.Client() as client:
        if not wait_for_keycloak(client):
            print(f"Error: Keycloak failed to become ready at {KEYCLOAK_BASE_URL}", file=sys.stderr)
            return 1

        print("Obtaining administrative session...")
        admin_token = get_admin_token(client)

        print("Provisioning credentials for demo accounts...")
        for username in DEMO_USERS:
            set_user_password(client, admin_token, username, DEMO_PASSWORD)

        print("Keycloak demo bootstrap completed successfully.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
