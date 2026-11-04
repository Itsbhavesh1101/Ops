#!/usr/bin/env python3
"""Run the repository vulnerability gate with deterministic evidence output."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.release.build_production_release import TRIVY_SKIP_DIRS  # noqa: E402


def build_command(trivy_bin: str, output_path: Path) -> list[str]:
    command = [
        trivy_bin,
        "fs",
        "--format",
        "json",
        "--output",
        str(output_path),
        "--scanners",
        "vuln",
        "--severity",
        "CRITICAL,HIGH",
        "--exit-code",
        "1",
    ]
    for directory in TRIVY_SKIP_DIRS:
        command.extend(("--skip-dirs", directory))
    command.append(".")
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/verification/security/trivy-fs.json"),
    )
    args = parser.parse_args()

    trivy_bin = shutil.which("trivy")
    if trivy_bin is None:
        print("Trivy is required on PATH for the vulnerability gate.", file=sys.stderr)
        return 2

    output_path = args.output
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    completed = subprocess.run(build_command(trivy_bin, output_path), cwd=REPO_ROOT)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
