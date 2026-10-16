"""Deterministic Release Candidate Manifest Generator for Phase 8H.

Inventories all candidate source content (RC_SOURCE_CONTENT), computes SHA-256
for each file, classifies files, and outputs:
- docs/phase-8/release/RC_CONTENT_MANIFEST.json (clean source manifest with NO self-references)
- docs/phase-8/release/RC_CONTENT_MANIFEST.sha256 (matches RC_CONTENT_MANIFEST.json exactly)
- Computes canonical RC_CONTENT_ID = sha256(RC_CONTENT_MANIFEST.json bytes on disk)
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path("F:/operations-engine").resolve()

# Directories/files belonging to candidate release source boundary
CANDIDATE_ROOTS = [
    "app",
    "workflows",
    "alembic",
    "alembic.ini",
    "scripts",
    "docker",
    "docker-compose.yml",
    ".dockerignore",
    "pyproject.toml",
    "README.md",
    ".env.example",
    "docs",
    "tests",
    "ui/operations-ui",
]

# Paths/patterns strictly excluded from release candidate
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "scratch",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    "dist",
    "htmlcov",
    ".coverage",
    ".gemini",
    ".vscode",
    ".idea",
}

# Subdirectories excluded to prevent circular manifest recursion and separate
# RC_SOURCE_CONTENT from RC_SECURITY_ATTESTATIONS
EXCLUDED_PATHS = {
    "docs/phase-8/release",
}


def classify_file(rel_path: str) -> str:
    norm = rel_path.replace("\\", "/")
    dep_specs = (
        "pyproject.toml",
        "ui/operations-ui/package.json",
        "ui/operations-ui/package-lock.json",
    )
    config_templates = (
        ".env.example",
        "ui/operations-ui/vite.config.ts",
        "ui/operations-ui/tsconfig.json",
        "ui/operations-ui/tsconfig.node.json",
    )
    if norm.startswith("app/"):
        return "core_application"
    elif norm.startswith("workflows/"):
        return "workflow_definition"
    elif norm.startswith("alembic/") or norm == "alembic.ini":
        return "database_migration"
    elif norm.startswith("docker/") or norm in ("docker-compose.yml", ".dockerignore"):
        return "deployment_manifest"
    elif norm in dep_specs:
        return "dependency_specification"
    elif norm in config_templates:
        return "configuration_template"
    elif norm.startswith("scripts/"):
        return "operational_script"
    elif norm.startswith("docs/") or norm in ("README.md", "CHAT_HISTORY.md"):
        return "production_documentation"
    elif norm.startswith("tests/"):
        return "verification_test"
    elif norm.startswith("ui/operations-ui/"):
        return "ui_frontend"
    else:
        return "release_metadata"


def get_tracked_files() -> set[str]:
    res = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {line.strip().replace("\\", "/") for line in res.stdout.splitlines() if line.strip()}


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def is_excluded(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    parts = set(norm.split("/"))
    if parts & EXCLUDED_PARTS:
        return True
    for prefix in EXCLUDED_PATHS:
        if norm == prefix or norm.startswith(prefix + "/"):
            return True
    if norm.endswith(".pyc") or norm.endswith(".pyo"):
        return True
    return False


def build_manifest() -> str:
    tracked = get_tracked_files()
    candidate_files = []

    for item in CANDIDATE_ROOTS:
        full_path = REPO_ROOT / item
        if not full_path.exists():
            continue
        if full_path.is_file():
            rel = item.replace("\\", "/")
            if not is_excluded(rel):
                candidate_files.append(rel)
        elif full_path.is_dir():
            for root, dirs, files in os.walk(full_path):
                # prune dirs
                dirs[:] = [d for d in dirs if d not in EXCLUDED_PARTS]
                for f in files:
                    file_path = Path(root) / f
                    rel = file_path.relative_to(REPO_ROOT).as_posix()
                    if not is_excluded(rel):
                        candidate_files.append(rel)

    # Sort files deterministically
    candidate_files = sorted(list(set(candidate_files)))

    manifest_entries = []
    tracked_count = 0
    untracked_count = 0
    total_bytes = 0

    for rel in candidate_files:
        p = REPO_ROOT / rel
        size = p.stat().st_size
        sha = compute_sha256(p)
        is_tracked = rel in tracked
        if is_tracked:
            tracked_count += 1
        else:
            untracked_count += 1
        total_bytes += size
        classification = classify_file(rel)

        manifest_entries.append({
            "classification": classification,
            "git_status": "tracked" if is_tracked else "untracked",
            "path": rel,
            "sha256": sha,
            "size_bytes": size,
        })

    # The manifest strictly describes RC_SOURCE_CONTENT with NO self-reference key.
    manifest_obj = {
        "candidate_name": "operations-engine",
        "candidate_version": "0.1.0",
        "files": manifest_entries,
        "git_branch": "master",
        "git_head": "69349b093a71ab5b8bbb83ec9d97e4f7de6b90b5",
        "manifest_version": "1.0.0",
        "summary": {
            "total_files": len(manifest_entries),
            "total_size_bytes": total_bytes,
            "tracked_files": tracked_count,
            "untracked_files": untracked_count,
        },
        "tree_state": "dirty_preserved",
    }

    out_dir = REPO_ROOT / "docs" / "phase-8" / "release"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = out_dir / "RC_CONTENT_MANIFEST.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_obj, f, indent=2, sort_keys=True)

    # Compute FINAL RC_CONTENT_ID directly from the manifest file bytes on disk
    rc_content_id = compute_sha256(manifest_path)

    sha_path = out_dir / "RC_CONTENT_MANIFEST.sha256"
    with open(sha_path, "w", encoding="utf-8") as f:
        f.write(f"{rc_content_id}  RC_CONTENT_MANIFEST.json\n")

    print(f"Manifest written to: {manifest_path}")
    print(f"Total candidate files: {len(manifest_entries)}")
    print(f"Tracked candidate files: {tracked_count}")
    print(f"Untracked candidate files: {untracked_count}")
    print(f"Total size: {total_bytes} bytes")
    print(f"FINAL RC_CONTENT_ID: {rc_content_id}")
    return rc_content_id


if __name__ == "__main__":
    build_manifest()
