"""complete task status and footage records"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "8e4b7a0d2c15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO task_status (task_status_id, name) VALUES (4, 'complete') ON CONFLICT DO NOTHING"
    )
    op.create_table(
        "task_footage",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), server_default="processing", nullable=False),
        sa.Column("object_path", sa.Text()),
        sa.Column("public_url", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["task.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id"),
    )


def downgrade() -> None:
    op.drop_table("task_footage")
    op.execute("DELETE FROM task_status WHERE task_status_id = 4")
