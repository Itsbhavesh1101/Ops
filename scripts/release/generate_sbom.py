"""CycloneDX 1.5 JSON SBOM Generator for Phase 8H.

Generates authoritative Software Bill of Materials (SBOM) covering:
1. Application metadata
2. Container base image (python:3.11-slim)
3. Python production runtime dependencies (scope=required)
4. Python dev & evaluation dependencies (scope=optional)
5. Frontend runtime dependencies (scope=required)
6. Frontend dev dependencies (scope=optional)
"""

import importlib.metadata
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(
    os.environ.get("OPERATIONS_ENGINE_ROOT", Path(__file__).resolve().parents[2])
).resolve()

# Production runtime dependencies defined in pyproject.toml
PYTHON_PROD_DIRECT = [
    "fastapi",
    "uvicorn",
    "pydantic",
    "pydantic-settings",
    "structlog",
    "httpx",
    "pyjwt",
    "sqlalchemy",
    "asyncpg",
    "alembic",
    "aio-pika",
    "networkx",
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-http",
    "opentelemetry-instrumentation-fastapi",
    "pydantic-ai-slim",
    "openai",
    "ortools",
    "aiomysql",
    "google-auth",
    "defusedxml",
]

PYTHON_DEV = [
    "pytest",
    "pytest-asyncio",
    "pytest-cov",
    "ruff",
    "mypy",
    "pre-commit",
]

PYTHON_EVAL = [
    "deepeval",
    "locust",
    "psutil",
]


def get_pkg_info(pkg_name: str) -> dict:
    try:
        dist = importlib.metadata.distribution(pkg_name)
        version = dist.version
        metadata = dist.metadata
        license_name = metadata.get("License", "Unknown")
        description = metadata.get("Summary", "")
        return {
            "name": pkg_name,
            "version": version,
            "license": license_name,
            "description": description,
        }
    except importlib.metadata.PackageNotFoundError:
        return {
            "name": pkg_name,
            "version": "unknown",
            "license": "Unknown",
            "description": "",
        }


def generate_sbom(rc_content_id: str = "") -> dict:
    bom_serial = f"urn:uuid:{uuid.uuid4()}"
    timestamp = datetime.now(UTC).isoformat()

    components = []

    # 1. Base and Supporting Container Images
    py_ver = "3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce"
    components.append({
        "type": "container",
        "name": "python",
        "version": py_ver,
        "description": "Debian-based official minimal Python 3.11 runtime image",
        "purl": f"pkg:docker/python@3.11-slim?digest={py_ver.split('@')[1]}",
        "scope": "required",
        "properties": [
            {"name": "distribution", "value": "Debian GNU/Linux 13 (trixie)"},
            {"name": "runtime_role", "value": "production_base_image"},
        ],
    })
    pg_ver = "16.15-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
    components.append({
        "type": "container",
        "name": "postgres",
        "version": pg_ver,
        "description": "PostgreSQL 16 relational datastore",
        "purl": f"pkg:docker/postgres@16.15-alpine?digest={pg_ver.split('@')[1]}",
        "scope": "required",
        "properties": [
            {"name": "distribution", "value": "Alpine Linux"},
            {"name": "runtime_role", "value": "external_datastore"},
        ],
    })
    rmq_ver = (
        "3.13-management@sha256:e582c0bc7766f3342496d8485efb5a1df782b5ce3886ad017e2eaae442311f69"
    )
    components.append({
        "type": "container",
        "name": "rabbitmq",
        "version": rmq_ver,
        "description": "RabbitMQ message broker",
        "purl": f"pkg:docker/rabbitmq@3.13-management?digest={rmq_ver.split('@')[1]}",
        "scope": "required",
        "properties": [
            {"name": "distribution", "value": "Ubuntu 24.04 LTS"},
            {"name": "runtime_role", "value": "external_message_broker"},
        ],
    })
    flw_ver = (
        "flowable-rest:7.0.0@sha256:64a0d7c12a2bb9da1b3645c8150f6ddaba31a23b23c1f9f8af1d3d819fa9f8b6"
    )
    components.append({
        "type": "container",
        "name": "flowable",
        "version": flw_ver,
        "description": "Flowable BPMN 2.0 orchestration engine",
        "purl": f"pkg:docker/flowable-rest@7.0.0?digest={flw_ver.split('@')[1]}",
        "scope": "required",
        "properties": [
            {"name": "runtime_role", "value": "external_workflow_engine"},
        ],
    })
    otel_ver = (
        "0.162.0@sha256:39923a8e431bd1f57be82411999d389fcfe40857492e4365456d97a4c1f74be6"
    )
    components.append({
        "type": "container",
        "name": "opentelemetry-collector-contrib",
        "version": otel_ver,
        "description": "OpenTelemetry telemetry collector",
        "purl": (
            f"pkg:docker/opentelemetry-collector-contrib@0.162.0?digest={otel_ver.split('@')[1]}"
        ),
        "scope": "optional",
        "properties": [
            {"name": "runtime_role", "value": "observability_collector"},
        ],
    })
    prom_ver = "v3.15.0@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e"
    components.append({
        "type": "container",
        "name": "prometheus",
        "version": prom_ver,
        "description": "Prometheus metrics monitoring system",
        "purl": f"pkg:docker/prometheus@v3.15.0?digest={prom_ver.split('@')[1]}",
        "scope": "optional",
        "properties": [
            {"name": "runtime_role", "value": "observability_metrics"},
        ],
    })

    # 2. Python Production Dependencies
    seen_py_pkgs = set()
    for pkg in PYTHON_PROD_DIRECT:
        seen_py_pkgs.add(pkg.lower())
        info = get_pkg_info(pkg)
        comp = {
            "type": "library",
            "name": info["name"],
            "version": info["version"],
            "description": info["description"][:200] if info["description"] else "",
            "purl": f"pkg:pypi/{info['name']}@{info['version']}",
            "scope": "required",
            "properties": [
                {"name": "ecosystem", "value": "python"},
                {"name": "tier", "value": "production_runtime_direct"},
            ],
        }
        if info["license"] and info["license"] != "Unknown":
            comp["licenses"] = [{"license": {"name": info["license"]}}]
        components.append(comp)

    # Key transitive runtime dependencies
    transitive_runtime = [
        "starlette",
        "anyio",
        "cryptography",
        "greenlet",
        "typing-extensions",
        "sniffio",
        "h11",
        "certifi",
        "charset-normalizer",
        "idna",
        "urllib3",
        "aiormq",
        "pamqp",
        "protobuf",
        "absl-py",
        "immutabledict",
        "cachetools",
        "pyasn1",
        "pyasn1-modules",
        "rsa",
    ]
    for pkg in transitive_runtime:
        if pkg.lower() in seen_py_pkgs:
            continue
        seen_py_pkgs.add(pkg.lower())
        info = get_pkg_info(pkg)
        if info["version"] != "unknown":
            comp = {
                "type": "library",
                "name": info["name"],
                "version": info["version"],
                "description": info["description"][:200] if info["description"] else "",
                "purl": f"pkg:pypi/{info['name']}@{info['version']}",
                "scope": "required",
                "properties": [
                    {"name": "ecosystem", "value": "python"},
                    {"name": "tier", "value": "production_runtime_transitive"},
                ],
            }
            if info["license"] and info["license"] != "Unknown":
                comp["licenses"] = [{"license": {"name": info["license"]}}]
            components.append(comp)

    # 3. Python Dev Dependencies (scope=optional)
    for pkg in PYTHON_DEV:
        if pkg.lower() in seen_py_pkgs:
            continue
        seen_py_pkgs.add(pkg.lower())
        info = get_pkg_info(pkg)
        comp = {
            "type": "library",
            "name": info["name"],
            "version": info["version"],
            "description": info["description"][:200] if info["description"] else "",
            "purl": f"pkg:pypi/{info['name']}@{info['version']}",
            "scope": "optional",
            "properties": [
                {"name": "ecosystem", "value": "python"},
                {"name": "tier", "value": "development_testing"},
            ],
        }
        components.append(comp)

    # 4. Python Evaluation Dependencies (scope=optional)
    for pkg in PYTHON_EVAL:
        if pkg.lower() in seen_py_pkgs:
            continue
        seen_py_pkgs.add(pkg.lower())
        info = get_pkg_info(pkg)
        comp = {
            "type": "library",
            "name": info["name"],
            "version": info["version"],
            "description": info["description"][:200] if info["description"] else "",
            "purl": f"pkg:pypi/{info['name']}@{info['version']}",
            "scope": "optional",
            "properties": [
                {"name": "ecosystem", "value": "python"},
                {"name": "tier", "value": "optional_evaluation_tooling"},
            ],
        }
        components.append(comp)

    # 5. Frontend Runtime Dependencies (Node)
    ui_pkg_path = REPO_ROOT / "ui" / "operations-ui" / "package.json"
    if ui_pkg_path.exists():
        with open(ui_pkg_path, encoding="utf-8") as f:
            ui_pkg = json.load(f)

        for name, ver in ui_pkg.get("dependencies", {}).items():
            components.append({
                "type": "library",
                "name": name,
                "version": ver.lstrip("^~"),
                "purl": f"pkg:npm/{name}@{ver.lstrip('^~')}",
                "scope": "required",
                "properties": [
                    {"name": "ecosystem", "value": "npm"},
                    {"name": "tier", "value": "frontend_runtime"},
                ],
            })

        for name, ver in ui_pkg.get("devDependencies", {}).items():
            components.append({
                "type": "library",
                "name": name,
                "version": ver.lstrip("^~"),
                "purl": f"pkg:npm/{name}@{ver.lstrip('^~')}",
                "scope": "optional",
                "properties": [
                    {"name": "ecosystem", "value": "npm"},
                    {"name": "tier", "value": "frontend_dev_testing"},
                ],
            })

    app_desc = (
        "IR Infotech E-7 Operations Engine: "
        "Unified Task Intelligence, Workflow, and Scheduling Orchestration"
    )
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": bom_serial,
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "tools": [
                {
                    "vendor": "CycloneDX",
                    "name": "cyclonedx-python-lib",
                    "version": "11.12.0",
                }
            ],
            "component": {
                "type": "application",
                "name": "operations-engine",
                "version": "0.1.0",
                "description": app_desc,
                "purl": "pkg:generic/operations-engine@0.1.0",
                "properties": [
                    {"name": "scanned_rc_content_id", "value": rc_content_id},
                    {"name": "git_branch", "value": "master"},
                    {"name": "git_head", "value": "69349b093a71ab5b8bbb83ec9d97e4f7de6b90b5"},
                    {"name": "working_tree", "value": "DIRTY_PRESERVED"},
                ],
            },
        },
        "components": components,
    }

    out_file = REPO_ROOT / "docs" / "phase-8" / "release" / "SBOM.cyclonedx.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(sbom, f, indent=2, sort_keys=True)

    required_count = sum(1 for c in components if c.get("scope") == "required")
    optional_count = sum(1 for c in components if c.get("scope") == "optional")

    print(f"SBOM written to: {out_file}")
    print(f"Total components: {len(components)}")
    print(f"Runtime required components: {required_count}")
    print(f"Dev/optional components: {optional_count}")
    return sbom


if __name__ == "__main__":
    import sys

    rc_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if not rc_id:
        sha_file = REPO_ROOT / "docs" / "phase-8" / "release" / "RC_CONTENT_MANIFEST.sha256"
        if sha_file.exists():
            rc_id = sha_file.read_text(encoding="utf-8").split()[0]
    generate_sbom(rc_id)
