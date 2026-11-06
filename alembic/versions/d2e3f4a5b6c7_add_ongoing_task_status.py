"""add ongoing task status

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO task_status (task_status_id, name) "
        "VALUES (5, 'ongoing') ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM task_status WHERE task_status_id = 5")
