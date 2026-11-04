"""persist broadcast monitoring state and samples"""

import sqlalchemy as sa

from alembic import op

revision = "b31c2d9e4a11"
down_revision = "4f7c9d2a1b30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "broadcasts", sa.Column("monitoring_started_at", sa.DateTime(timezone=True))
    )
    op.create_table(
        "broadcast_count_samples",
        sa.Column("sample_id", sa.UUID(), primary_key=True),
        sa.Column(
            "broadcast_id",
            sa.UUID(),
            sa.ForeignKey("broadcasts.broadcast_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_broadcast_count_samples_broadcast_time",
        "broadcast_count_samples",
        ["broadcast_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_broadcast_count_samples_broadcast_time",
        table_name="broadcast_count_samples",
    )
    op.drop_table("broadcast_count_samples")
    op.drop_column("broadcasts", "monitoring_started_at")
