"""zone: single threshold + is_active_task; users: home zone; task: instruction

- zone: warning_threshold/crowded_threshold -> one `threshold` (backfilled from
  crowded_threshold), map_image_url dropped, is_active_task added (backfilled
  from each zone's latest task).
- zone_logs: rows are deleted with their zone (ON DELETE CASCADE).
- users: zone_id (home zone) and last_location (beacon, not wired yet) added,
  both nullable and cleared if the zone is deleted; email_verified_at,
  updated_at and last_login_at dropped.
- task: instruction (required, max 50 chars); existing rows get ''.

Downgrade cannot recover dropped values: it restores the columns with defaults.

Revision ID: 8e4b7a0d2c15
Revises: 5c8d2e1f9a63
Create Date: 2026-11-05 10:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8e4b7a0d2c15"
down_revision: str | Sequence[str] | None = "5c8d2e1f9a63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A zone is active while its latest task is still open, or closed with at least
# one respondent and not yet finished (task_status ids: 1 open, 2 closed).
BACKFILL_ACTIVE = """
UPDATE zone z SET is_active_task = true
FROM (
    SELECT DISTINCT ON (zone_id) task_id, zone_id, task_status_id, expires_at
    FROM task ORDER BY zone_id, created_at DESC
) latest
WHERE latest.zone_id = z.zone_id AND (
    (latest.task_status_id = 1 AND latest.expires_at > now())
    OR (
        latest.task_status_id IN (1, 2)
        AND EXISTS (SELECT 1 FROM task_respondents r WHERE r.task_id = latest.task_id)
    )
)
"""


def upgrade() -> None:
    # --- zone ---
    op.add_column("zone", sa.Column("threshold", sa.Integer(), nullable=True))
    op.execute("UPDATE zone SET threshold = crowded_threshold")
    op.alter_column("zone", "threshold", nullable=False)
    op.drop_constraint("zones_warning_threshold_check", "zone", type_="check")
    op.drop_constraint("zones_crowded_threshold_check", "zone", type_="check")
    op.drop_column("zone", "warning_threshold")
    op.drop_column("zone", "crowded_threshold")
    op.drop_column("zone", "map_image_url")
    op.create_check_constraint("zone_threshold_check", "zone", "threshold > 0")
    op.add_column(
        "zone",
        sa.Column(
            "is_active_task", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.execute(BACKFILL_ACTIVE)

    # --- zone_logs: history goes with its zone ---
    op.drop_constraint("zone_logs_zone_id_fkey", "zone_logs", type_="foreignkey")
    op.create_foreign_key(
        "zone_logs_zone_id_fkey",
        "zone_logs",
        "zone",
        ["zone_id"],
        ["zone_id"],
        ondelete="CASCADE",
    )

    # --- users ---
    op.add_column("users", sa.Column("zone_id", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("last_location", sa.Text(), nullable=True))
    op.create_foreign_key(
        "users_zone_id_fkey",
        "users",
        "zone",
        ["zone_id"],
        ["zone_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "users_last_location_fkey",
        "users",
        "zone",
        ["last_location"],
        ["zone_id"],
        ondelete="SET NULL",
    )
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "last_login_at")

    # --- task ---
    op.add_column(
        "task",
        sa.Column(
            "instruction", sa.String(length=50), nullable=False, server_default=""
        ),
    )
    op.alter_column("task", "instruction", server_default=None)


def downgrade() -> None:
    op.drop_column("task", "instruction")

    op.add_column(
        "users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("users_last_location_fkey", "users", type_="foreignkey")
    op.drop_constraint("users_zone_id_fkey", "users", type_="foreignkey")
    op.drop_column("users", "last_location")
    op.drop_column("users", "zone_id")

    op.drop_constraint("zone_logs_zone_id_fkey", "zone_logs", type_="foreignkey")
    op.create_foreign_key(
        "zone_logs_zone_id_fkey", "zone_logs", "zone", ["zone_id"], ["zone_id"]
    )

    op.drop_column("zone", "is_active_task")
    op.drop_constraint("zone_threshold_check", "zone", type_="check")
    op.add_column(
        "zone",
        sa.Column("map_image_url", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column("zone", sa.Column("crowded_threshold", sa.Integer(), nullable=True))
    op.add_column("zone", sa.Column("warning_threshold", sa.Integer(), nullable=True))
    # Old rule: 0 < warning < crowded. Rebuild both from the single threshold.
    op.execute(
        "UPDATE zone SET crowded_threshold = GREATEST(threshold, 2), "
        "warning_threshold = GREATEST(1, GREATEST(threshold, 2) / 2)"
    )
    op.alter_column("zone", "crowded_threshold", nullable=False)
    op.alter_column("zone", "warning_threshold", nullable=False)
    op.alter_column("zone", "map_image_url", server_default=None)
    op.create_check_constraint(
        "zones_warning_threshold_check", "zone", "warning_threshold > 0"
    )
    op.create_check_constraint(
        "zones_crowded_threshold_check",
        "zone",
        "crowded_threshold > warning_threshold",
    )
    op.drop_column("zone", "threshold")
