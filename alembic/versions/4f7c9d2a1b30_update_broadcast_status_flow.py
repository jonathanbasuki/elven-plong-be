"""update broadcast status flow"""

from alembic import op

revision = "4f7c9d2a1b30"
down_revision = "8d4c2a1e7f90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("broadcast_status_check", "broadcasts", type_="check")
    op.execute("UPDATE broadcasts SET status = 'canceled' WHERE status = 'closed'")
    op.create_check_constraint(
        "broadcast_status_check",
        "broadcasts",
        "status IN ('open', 'in_progress', 'complete', 'finished', 'canceled')",
    )


def downgrade() -> None:
    op.drop_constraint("broadcast_status_check", "broadcasts", type_="check")
    op.create_check_constraint(
        "broadcast_status_check",
        "broadcasts",
        "status IN ('open', 'closed', 'complete', 'finished')",
    )
