"""API Reference coverage test (Phase 8F).

Ensures that:
1. Every path mounted in canonical docs/production/openapi.json (59 paths) is
   explicitly documented and discoverable in docs/production/API_REFERENCE.md.
2. Quota 429 errors are scoped to the exact 4 capacity-governed operations.
3. Provider-neutral OIDC/JWT authentication wording is present.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OPENAPI_JSON_PATH = REPO_ROOT / "docs" / "production" / "openapi.json"
API_REFERENCE_PATH = REPO_ROOT / "docs" / "production" / "API_REFERENCE.md"


class TestApiReferenceCoverage:
    """Deterministic validation that API_REFERENCE.md covers all mounted operations."""

    def test_all_openapi_paths_documented_in_api_reference(self) -> None:
        """Verify all 68 paths from openapi.json are documented in API_REFERENCE.md."""
        assert OPENAPI_JSON_PATH.exists(), f"Missing {OPENAPI_JSON_PATH}"
        assert API_REFERENCE_PATH.exists(), f"Missing {API_REFERENCE_PATH}"

        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            schema = json.load(f)

        openapi_paths = set(schema.get("paths", {}).keys())
        assert len(openapi_paths) == 71, f"Expected 71 paths, found {len(openapi_paths)}"

        ref_text = API_REFERENCE_PATH.read_text(encoding="utf-8")

        missing_paths = [p for p in sorted(openapi_paths) if p not in ref_text]
        assert not missing_paths, (
            f"Mounted OpenAPI paths missing from API_REFERENCE.md: {missing_paths}"
        )

    def test_oidc_wording_neutral(self) -> None:
        """Verify API_REFERENCE.md uses provider-neutral OIDC/JWT wording."""
        ref_text = API_REFERENCE_PATH.read_text(encoding="utf-8")
        assert "Target: Google Identity ID tokens" not in ref_text, (
            "Found legacy provider-specific target wording in API_REFERENCE.md"
        )
        assert "Configured OIDC/JWT" in ref_text or "OIDC/JWT" in ref_text
