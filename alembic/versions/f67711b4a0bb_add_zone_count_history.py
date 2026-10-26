"""add zone_count_history

Revision ID: f67711b4a0bb
Revises: f9fe68ec2955
Create Date: 2026-10-26 15:57:33.738636

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f67711b4a0bb"
down_revision: str | Sequence[str] | None = "f9fe68ec2955"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "zone_count_history",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("zone_id", sa.String(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("avg_count", sa.Float(), nullable=False),
        sa.Column("peak_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["zones.zone_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "zone_id", "bucket_start", name="uq_zone_count_history_zone_bucket"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("zone_count_history")
