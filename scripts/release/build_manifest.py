"""Deterministic Release Candidate Manifest Generator for Phase 8H.

Inventories all candidate source content (RC_SOURCE_CONTENT), computes SHA-256
for each file, classifies files, and outputs:
- docs/phase-8/release/RC_CONTENT_MANIFEST.json (clean manifest with pure content identity)
- docs/phase-8/release/RC_CONTENT_MANIFEST.sha256 (matches RC_CONTENT_MANIFEST.json exactly)
- docs/phase-8/release/RC_PROVENANCE.json (non-identity git & working tree provenance)
- Computes canonical RC_CONTENT_ID = sha256(RC_CONTENT_MANIFEST.json bytes on disk)
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path


def get_repo_root() -> Path:
    env_root = os.environ.get("OPERATIONS_ENGINE_ROOT")
    if env_root and Path(env_root).is_dir():
        return Path(env_root).resolve()
    script_root = Path(__file__).resolve().parents[2]
    if (script_root / "docker-compose.yml").is_file():
        return script_root
    if (Path.cwd() / "docker-compose.yml").is_file():
        return Path.cwd().resolve()
    return Path("F:/operations-engine").resolve()


REPO_ROOT = get_repo_root()

# Directories/files belonging to candidate release source boundary
CANDIDATE_ROOTS = [
    ".gitattributes",
    "app",
    "workflows",
    "alembic",
    "alembic.ini",
    "scripts",
    "docker",
    "docker-compose.yml",
    ".dockerignore",
    "pyproject.toml",
    "requirements.lock.txt",
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
        "requirements.lock.txt",
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
    elif (
        norm.startswith("docker/")
        or norm in ("docker-compose.yml", ".dockerignore", ".gitattributes")
    ):
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


def get_git_provenance() -> dict:
    repo = get_repo_root()
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        branch = "detached"

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        head = "unknown"

    try:
        res = subprocess.run(
            ["git", "ls-files"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        tracked = {
            line.strip().replace("\\", "/")
            for line in res.stdout.splitlines()
            if line.strip()
        }
    except Exception:
        tracked = set()

    try:
        status_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        tree_state = "dirty" if status_res.stdout.strip() else "clean"
    except Exception:
        tree_state = "unknown"

    return {
        "git_branch": branch or "detached",
        "git_head": head,
        "tree_state": tree_state,
        "tracked_files": tracked,
    }


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
    repo_root = get_repo_root()
    provenance_data = get_git_provenance()
    tracked = provenance_data.pop("tracked_files")

    candidate_files = []

    for item in CANDIDATE_ROOTS:
        full_path = repo_root / item
        if not full_path.exists():
            continue
        if full_path.is_file():
            rel = item.replace("\\", "/")
            if not is_excluded(rel):
                candidate_files.append(rel)
        elif full_path.is_dir():
            for root, dirs, files in os.walk(full_path):
                dirs[:] = [d for d in dirs if d not in EXCLUDED_PARTS]
                for f in files:
                    file_path = Path(root) / f
                    rel = file_path.relative_to(repo_root).as_posix()
                    if not is_excluded(rel):
                        candidate_files.append(rel)

    candidate_files = sorted(list(set(candidate_files)))

    manifest_entries = []
    tracked_count = 0
    untracked_count = 0
    total_bytes = 0

    for rel in candidate_files:
        p = repo_root / rel
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
            "path": rel,
            "sha256": sha,
            "size_bytes": size,
        })

    manifest_obj = {
        "candidate_name": "operations-engine",
        "candidate_version": "0.1.0",
        "files": manifest_entries,
        "manifest_version": "1.0.0",
        "summary": {
            "total_files": len(manifest_entries),
            "total_size_bytes": total_bytes,
        },
    }

    out_dir = repo_root / "docs" / "phase-8" / "release"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = out_dir / "RC_CONTENT_MANIFEST.json"
    manifest_bytes = (json.dumps(manifest_obj, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with open(manifest_path, "wb") as f:
        f.write(manifest_bytes)

    rc_content_id = hashlib.sha256(manifest_bytes).hexdigest()

    sha_path = out_dir / "RC_CONTENT_MANIFEST.sha256"
    with open(sha_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{rc_content_id}  RC_CONTENT_MANIFEST.json\n")

    # Non-identity provenance metadata saved separately
    provenance_data.update({
        "candidate_name": "operations-engine",
        "candidate_version": "0.1.0",
        "rc_content_id": rc_content_id,
        "summary": {
            "total_files": len(manifest_entries),
            "total_size_bytes": total_bytes,
            "tracked_files": tracked_count,
            "untracked_files": untracked_count,
        },
    })
    provenance_path = out_dir / "RC_PROVENANCE.json"
    provenance_bytes = (
        json.dumps(provenance_data, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    with open(provenance_path, "wb") as f:
        f.write(provenance_bytes)

    print(f"Manifest written to: {manifest_path}")
    print(f"Provenance written to: {provenance_path}")
    print(f"Total candidate files: {len(manifest_entries)}")
    print(f"Tracked candidate files: {tracked_count}")
    print(f"Untracked candidate files: {untracked_count}")
    print(f"Total size: {total_bytes} bytes")
    print(f"FINAL RC_CONTENT_ID: {rc_content_id}")
    return rc_content_id


if __name__ == "__main__":
    build_manifest()
