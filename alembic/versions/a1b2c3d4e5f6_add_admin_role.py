"""add admin role

Revision ID: a1b2c3d4e5f6
Revises: f67711b4a0bb
Create Date: 2026-11-03 20:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f67711b4a0bb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add admin role with id=3."""
    op.bulk_insert(
        sa.table(
            "roles",
            sa.column("id", sa.SmallInteger()),
            sa.column("name", sa.String(length=32)),
        ),
        [{"id": 3, "name": "admin"}],
    )


def downgrade() -> None:
    """Remove admin role. Fails if admin users exist (FK RESTRICT)."""
    op.execute("DELETE FROM roles WHERE id = 3")
