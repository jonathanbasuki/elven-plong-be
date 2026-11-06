"""status text columns -> lookup tables (task_status, task_respondent_status, user_status)

Each text status becomes a foreign key to a seeded lookup table with fixed ids,
backfilled from the old text. The ids are part of the contract: the code and the
partial index `one_open_task_per_zone` compare against them.

Revision ID: 5c8d2e1f9a63
Revises: 3f1a9c2e7b40
Create Date: 2026-11-05 10:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5c8d2e1f9a63"
down_revision: str | Sequence[str] | None = "3f1a9c2e7b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASK_STATUSES = [
    (1, "open"),
    (2, "closed"),
    (3, "finished"),
    (4, "complete"),
    (5, "ongoing"),
]
RESPONDENT_STATUSES = [(1, "ongoing"), (2, "completed")]
USER_STATUSES = [(1, "idle"), (2, "delegating")]


def _lookup_table(name: str, rows: list[tuple[int, str]]) -> None:
    pk = f"{name}_id"
    op.create_table(
        name,
        sa.Column(pk, sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(pk),
        sa.UniqueConstraint("name"),
    )
    op.bulk_insert(
        sa.table(name, sa.column(pk, sa.SmallInteger()), sa.column("name", sa.String)),
        [{pk: i, "name": n} for i, n in rows],
    )


def _case(column: str, rows: list[tuple[int, str]]) -> str:
    whens = " ".join(f"WHEN '{n}' THEN {i}" for i, n in rows)
    return f"CASE {column} {whens} END"


def _add_status_fk(table: str, lookup: str, backfill_sql: str) -> None:
    col = f"{lookup}_id"
    op.add_column(table, sa.Column(col, sa.SmallInteger(), nullable=True))
    op.execute(f'UPDATE "{table}" SET {col} = {backfill_sql}')
    op.alter_column(table, col, nullable=False, server_default="1")
    op.create_foreign_key(f"{table}_{col}_fkey", table, lookup, [col], [col])


def upgrade() -> None:
    _lookup_table("task_status", TASK_STATUSES)
    _lookup_table("task_respondent_status", RESPONDENT_STATUSES)
    _lookup_table("user_status", USER_STATUSES)

    # task.status text -> task_status_id
    _add_status_fk("task", "task_status", _case("status", TASK_STATUSES))
    op.drop_index("one_open_broadcast_per_zone", table_name="task")
    op.drop_constraint("broadcast_status_check", "task", type_="check")
    op.drop_column("task", "status")
    op.create_index(
        "one_open_task_per_zone",
        "task",
        ["zone_id"],
        unique=True,
        postgresql_where=sa.text("task_status_id = 1"),
    )

    # A respondent is "completed" exactly when completed_at is set.
    _add_status_fk(
        "task_respondents",
        "task_respondent_status",
        "CASE WHEN completed_at IS NULL THEN 1 ELSE 2 END",
    )

    # users.status text -> user_status_id
    _add_status_fk("users", "user_status", _case("status", USER_STATUSES))
    op.drop_constraint("users_status_check", "users", type_="check")
    op.drop_column("users", "status")


def _restore_status_text(table: str, lookup: str, rows, default: str) -> None:
    col = f"{lookup}_id"
    op.add_column(table, sa.Column("status", sa.Text(), nullable=True))
    whens = " ".join(f"WHEN {i} THEN '{n}'" for i, n in rows)
    op.execute(f'UPDATE "{table}" SET status = CASE {col} {whens} END')
    op.alter_column(table, "status", nullable=False, server_default=default)
    op.drop_constraint(f"{table}_{col}_fkey", table, type_="foreignkey")
    op.drop_column(table, col)


def downgrade() -> None:
    _restore_status_text("users", "user_status", USER_STATUSES, "idle")
    op.create_check_constraint(
        "users_status_check", "users", "status IN ('idle', 'delegating')"
    )

    op.drop_constraint(
        "task_respondents_task_respondent_status_id_fkey",
        "task_respondents",
        type_="foreignkey",
    )
    op.drop_column("task_respondents", "task_respondent_status_id")

    op.drop_index("one_open_task_per_zone", table_name="task")
    _restore_status_text("task", "task_status", TASK_STATUSES, "open")
    op.create_check_constraint(
        "broadcast_status_check", "task", "status IN ('open', 'closed', 'finished')"
    )
    op.create_index(
        "one_open_broadcast_per_zone",
        "task",
        ["zone_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    op.drop_table("user_status")
    op.drop_table("task_respondent_status")
    op.drop_table("task_status")
