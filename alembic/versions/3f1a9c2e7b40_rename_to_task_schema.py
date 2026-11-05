"""rename broadcast tables to task and align table/column/constraint names

Pure renames, no data change. Hand-written: autogenerate would read a rename as
drop + create and lose the rows.

Revision ID: 3f1a9c2e7b40
Revises: a1b2c3d4e5f6
Create Date: 2026-11-05 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "3f1a9c2e7b40"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (old table, new table)
TABLES = [
    ("zones", "zone"),
    ("zone_count_history", "zone_logs"),
    ("broadcasts", "task"),
    ("broadcast_responses", "task_respondents"),
]

# (table after rename, old column, new column)
COLUMNS = [
    ("zone_logs", "id", "zone_log_id"),
    ("task", "broadcast_id", "task_id"),
    ("task", "staff_needed", "total_staff"),
    ("task_respondents", "broadcast_id", "task_id"),
    ("users", "id", "user_id"),
    ("roles", "id", "role_id"),
]

# (table after rename, old constraint, new constraint). Renaming a primary key or
# unique constraint also renames its index.
CONSTRAINTS = [
    ("zone", "zones_pkey", "zone_pkey"),
    ("zone_logs", "zone_count_history_pkey", "zone_logs_pkey"),
    ("zone_logs", "zone_count_history_zone_id_fkey", "zone_logs_zone_id_fkey"),
    ("zone_logs", "uq_zone_count_history_zone_bucket", "uq_zone_logs_zone_bucket"),
    ("task", "broadcasts_pkey", "task_pkey"),
    ("task", "broadcasts_zone_id_fkey", "task_zone_id_fkey"),
    ("task", "broadcast_staff_needed_check", "task_total_staff_check"),
    ("task_respondents", "broadcast_responses_pkey", "task_respondents_pkey"),
    (
        "task_respondents",
        "broadcast_responses_broadcast_id_fkey",
        "task_respondents_task_id_fkey",
    ),
    (
        "task_respondents",
        "broadcast_responses_staff_id_fkey",
        "task_respondents_staff_id_fkey",
    ),
    (
        "task_respondents",
        "broadcast_response_completed_check",
        "task_respondents_completed_check",
    ),
]

# (old index, new index)
INDEXES = [("idx_broadcast_responses_broadcast", "idx_task_respondents_task")]


def upgrade() -> None:
    for old, new in TABLES:
        op.rename_table(old, new)
    for table, old, new in COLUMNS:
        op.alter_column(table, old, new_column_name=new)
    for table, old, new in CONSTRAINTS:
        op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT {old} TO {new}')
    for old, new in INDEXES:
        op.execute(f"ALTER INDEX {old} RENAME TO {new}")


def downgrade() -> None:
    for old, new in INDEXES:
        op.execute(f"ALTER INDEX {new} RENAME TO {old}")
    for table, old, new in CONSTRAINTS:
        op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT {new} TO {old}')
    for table, old, new in COLUMNS:
        op.alter_column(table, new, new_column_name=old)
    for old, new in TABLES:
        op.rename_table(new, old)
