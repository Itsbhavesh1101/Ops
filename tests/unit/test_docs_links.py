"""Internal documentation link and file path validation test (Phase 8F).

Parses markdown documents in docs/production/ and validates that all local
file references, links, and cross-references point to real files on disk.
Zero external network calls required.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROD_DOCS_DIR = REPO_ROOT / "docs" / "production"

# Matches markdown links [label](path)
MD_LINK_REGEX = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def extract_internal_links(file_path: Path) -> list[str]:
    """Extract local file links from markdown content."""
    text = file_path.read_text(encoding="utf-8")
    links: list[str] = []
    for match in MD_LINK_REGEX.finditer(text):
        target = match.group(2)
        # Skip external http/https URLs or mailto
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        links.append(target)
    return links


class TestDocumentationLinks:
    """Verify internal documentation links point to existing files."""

    def test_production_docs_directory_exists(self) -> None:
        """Verify docs/production exists and has all required canonical files."""
        assert PROD_DOCS_DIR.exists()
        required_files = [
            "API_REFERENCE.md",
            "openapi.json",
            "ERD.md",
            "erd.mmd",
            "WORKFLOW_AUTHORING_GUIDE.md",
            "CONSTRAINT_CATALOG.md",
            "MODEL_EVALUATION_GUIDE.md",
            "DEPLOYMENT_RUNBOOK.md",
            "INCIDENT_RESPONSE_GUIDE.md",
            "DOCUMENTATION_INDEX.md",
        ]
        for name in required_files:
            file_path = PROD_DOCS_DIR / name
            assert file_path.exists(), f"Missing canonical production doc: {name}"

    def test_internal_links_in_production_docs_resolve(self) -> None:
        """Verify all internal file links in docs/production/ resolve to actual files."""
        broken_links: list[tuple[str, str]] = []

        for md_file in PROD_DOCS_DIR.glob("*.md"):
            links = extract_internal_links(md_file)
            for link in links:
                # Handle file:/// URLs
                clean_path = link.split("#")[0]  # Remove anchor
                if clean_path.startswith("file:///"):
                    # Extract local path from file:/// URI
                    raw_path = clean_path.replace("file:///", "")
                    # On Windows, e.g. f:/operations-engine/...
                    resolved_file = Path(raw_path)
                elif clean_path.startswith("/"):
                    resolved_file = REPO_ROOT / clean_path.lstrip("/")
                else:
                    resolved_file = (md_file.parent / clean_path).resolve()

                if not resolved_file.exists():
                    broken_links.append((str(md_file.name), link))

        assert not broken_links, f"Found broken internal doc links: {broken_links}"
