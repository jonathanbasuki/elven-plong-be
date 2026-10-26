"""create crowd dispatch tables

Revision ID: 7b5b2f7c1d21
Revises: ad950fffbf32
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7b5b2f7c1d21"
down_revision: str | Sequence[str] | None = "ad950fffbf32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "zones",
        sa.Column("zone_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("map_image_url", sa.Text(), nullable=False),
        sa.Column("polygon", postgresql.JSONB(), nullable=False),
        sa.Column("warning_threshold", sa.Integer(), nullable=False),
        sa.Column("crowded_threshold", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "warning_threshold > 0", name="zones_warning_threshold_check"
        ),
        sa.CheckConstraint(
            "crowded_threshold > warning_threshold",
            name="zones_crowded_threshold_check",
        ),
        sa.PrimaryKeyConstraint("zone_id"),
    )
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
    op.create_table(
        "broadcasts",
        sa.Column(
            "broadcast_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("zone_id", sa.Text(), nullable=False),
        sa.Column("staff_needed", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("staff_needed > 0", name="broadcast_staff_needed_check"),
        sa.CheckConstraint(
            "status IN ('open', 'closed', 'finished')", name="broadcast_status_check"
        ),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.zone_id"]),
        sa.PrimaryKeyConstraint("broadcast_id"),
    )
    op.create_index(
        "one_open_broadcast_per_zone",
        "broadcasts",
        ["zone_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "broadcast_responses",
        sa.Column("broadcast_id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column(
            "responded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= responded_at",
            name="broadcast_response_completed_check",
        ),
        sa.ForeignKeyConstraint(["broadcast_id"], ["broadcasts.broadcast_id"]),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.staff_id"]),
        sa.PrimaryKeyConstraint("broadcast_id", "staff_id"),
    )
    op.create_index(
        "idx_broadcast_responses_broadcast", "broadcast_responses", ["broadcast_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_broadcast_responses_broadcast", table_name="broadcast_responses")
    op.drop_table("broadcast_responses")
    op.drop_index("one_open_broadcast_per_zone", table_name="broadcasts")
    op.drop_table("broadcasts")
    op.drop_table("staff")
    op.drop_table("zones")
