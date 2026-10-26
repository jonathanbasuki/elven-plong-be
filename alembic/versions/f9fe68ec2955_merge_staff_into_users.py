"""merge staff into users

Revision ID: f9fe68ec2955
Revises: 7b5b2f7c1d21
Create Date: 2026-10-26 10:08:16.323508

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9fe68ec2955"
down_revision: str | Sequence[str] | None = "7b5b2f7c1d21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column("status", sa.Text(), server_default="idle", nullable=False),
    )
    op.add_column("users", sa.Column("push_token", sa.Text(), nullable=True))
    op.create_check_constraint(
        "users_status_check", "users", "status IN ('idle', 'delegating')"
    )

    op.drop_constraint(
        "broadcast_responses_staff_id_fkey", "broadcast_responses", type_="foreignkey"
    )
    op.create_foreign_key(
        "broadcast_responses_staff_id_fkey",
        "broadcast_responses",
        "users",
        ["staff_id"],
        ["id"],
    )

    op.drop_table("staff")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "staff",
        sa.Column(
            "staff_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="idle", nullable=False),
        sa.Column("push_token", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('idle', 'delegating')", name="staff_status_check"
        ),
        sa.PrimaryKeyConstraint("staff_id"),
    )

    op.drop_constraint(
        "broadcast_responses_staff_id_fkey", "broadcast_responses", type_="foreignkey"
    )
    op.create_foreign_key(
        "broadcast_responses_staff_id_fkey",
        "broadcast_responses",
        "staff",
        ["staff_id"],
        ["staff_id"],
    )

    op.drop_constraint("users_status_check", "users", type_="check")
    op.drop_column("users", "push_token")
    op.drop_column("users", "status")
