"""phase_1_persistence_baseline

Revision ID: 0001
Revises: None
Create Date: 2026-09-25 21:55:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema - empty baseline migration for Phase 1 persistence foundation."""
    pass


def downgrade() -> None:
    """Downgrade schema - empty baseline migration for Phase 1 persistence foundation."""
    pass
