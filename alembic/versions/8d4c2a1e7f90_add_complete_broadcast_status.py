"""add complete broadcast status"""

from collections.abc import Sequence

from alembic import op

revision: str = "8d4c2a1e7f90"
down_revision: str | Sequence[str] | None = (
    "f9fe68ec2955",
    "2c6c0c3f3e3a",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("broadcast_status_check", "broadcasts", type_="check")
    op.create_check_constraint(
        "broadcast_status_check",
        "broadcasts",
        "status IN ('open', 'closed', 'complete', 'finished')",
    )


def downgrade() -> None:
    op.drop_constraint("broadcast_status_check", "broadcasts", type_="check")
    op.create_check_constraint(
        "broadcast_status_check",
        "broadcasts",
        "status IN ('open', 'closed', 'finished')",
    )
