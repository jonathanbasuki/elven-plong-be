"""add snapshots"""

import sqlalchemy as sa

from alembic import op

revision = "2c6c0c3f3e3a"
down_revision = "f67711b4a0bb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "snapshots",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("broadcast_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("object_key", sa.String(), nullable=True),
        sa.Column("public_url", sa.String(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["broadcast_id"], ["broadcasts.broadcast_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_check_constraint(
        "snapshots_kind_check", "snapshots", "kind IN ('before', 'after')"
    )
    op.create_index("ix_snapshots_broadcast_id", "snapshots", ["broadcast_id"])
    op.create_index(
        "uq_snapshots_one_before",
        "snapshots",
        ["broadcast_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'before'"),
    )
    op.create_index(
        "uq_snapshots_one_after",
        "snapshots",
        ["broadcast_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'after'"),
    )


def downgrade() -> None:
    op.drop_index("uq_snapshots_one_after", table_name="snapshots")
    op.drop_index("uq_snapshots_one_before", table_name="snapshots")
    op.drop_index("ix_snapshots_broadcast_id", table_name="snapshots")
    op.drop_constraint("snapshots_kind_check", "snapshots", type_="check")
    op.drop_table("snapshots")
