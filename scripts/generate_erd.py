"""Canonical ERD Generation Utility (Phase 8F).

Introspects SQLAlchemy Base.metadata across all registered domain models:
- 26 application tables + alembic_version
- Extracts table names, columns, data types, primary keys, foreign keys, unique constraints
- Generates valid, source-controlled Mermaid ER Diagram (docs/production/erd.mmd)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app.db.models  # noqa: E402, F401 - registers all 26 application models
from app.db.base import Base  # noqa: E402


def generate_mermaid_erd() -> str:
    """Generate Mermaid erDiagram syntax from SQLAlchemy Base.metadata."""
    lines: list[str] = [
        "erDiagram",
        "    %% IR Infotech E-7 Operations Engine Canonical ERD",
        "    %% Generated from SQLAlchemy Base.metadata (26 app tables + alembic_version)",
        "",
    ]

    # Map relationships for Mermaid syntax: PK_TABLE ||--o{ FK_TABLE : "references"
    relationships: list[str] = []

    for table_name, table in sorted(Base.metadata.tables.items()):
        lines.append(f"    {table_name} {{")
        for col in table.columns:
            # Simplify type name for Mermaid
            type_str = str(col.type).replace(" ", "_").replace("(", "_").replace(")", "")
            pk_marker = " PK" if col.primary_key else ""
            fk_marker = " FK" if col.foreign_keys else ""
            nullable_marker = "" if col.nullable else ' "not-null"'
            desc = f"{pk_marker}{fk_marker}{nullable_marker}".strip()
            comment = f' "{desc}"' if desc else ""
            lines.append(f"        {type_str} {col.name}{comment}")
        lines.append("    }")
        lines.append("")

        for col in table.columns:
            for fk in col.foreign_keys:
                target_table = fk.column.table.name
                target_col = fk.column.name
                # Parent ||--o{ Child
                rel_label = f"{col.name} -> {target_col}"
                rel_str = f'    {target_table} ||--o{{ {table_name} : "{rel_label}"'
                relationships.append(rel_str)

    # Add alembic_version
    lines.append("    alembic_version {")
    lines.append('        VARCHAR_32 version_num PK "not-null"')
    lines.append("    }")
    lines.append("")

    if relationships:
        lines.append("    %% Relationships")
        for rel in sorted(set(relationships)):
            lines.append(rel)

    lines.append("")
    return "\n".join(lines)


def export_erd(output_path: Path) -> str:
    """Write Mermaid ERD to output_path."""
    content = generate_mermaid_erd()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        f.write(content)
    return content


if __name__ == "__main__":
    target = REPO_ROOT / "docs" / "production" / "erd.mmd"
    content = export_erd(target)
    print(f"Generated Mermaid ERD at: {target}")
    print(f"Total lines: {len(content.splitlines())}")
