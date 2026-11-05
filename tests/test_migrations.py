"""Tests for alembic migrations and related scripts."""

import importlib.util
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))


def test_migrations_form_one_linear_chain() -> None:
    script = _script()
    (head,) = script.get_heads()
    assert head == "8e4b7a0d2c15"
    chain = [rev.revision for rev in script.walk_revisions()]
    assert chain[:4] == ["8e4b7a0d2c15", "5c8d2e1f9a63", "3f1a9c2e7b40", "a1b2c3d4e5f6"]


def test_admin_role_migration_seeds_id_3() -> None:
    rev = _script().get_revision("a1b2c3d4e5f6")
    source = Path(rev.path).read_text()
    assert '"id": 3' in source and '"name": "admin"' in source
    assert "DELETE FROM roles WHERE id = 3" in source


def test_status_lookup_ids_match_the_code() -> None:
    """The partial index and the code compare against these fixed ids."""
    from plong_elven.db.models import TaskRespondentStatusId, TaskStatusId, UserStatusId

    spec = importlib.util.spec_from_file_location(
        "m", _script().get_revision("5c8d2e1f9a63").path
    )
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    as_enum = lambda rows: {name.upper(): i for i, name in rows}
    assert as_enum(mig.TASK_STATUSES) == {m.name: m.value for m in TaskStatusId}
    assert as_enum(mig.RESPONDENT_STATUSES) == {
        m.name: m.value for m in TaskRespondentStatusId
    }
    assert as_enum(mig.USER_STATUSES) == {m.name: m.value for m in UserStatusId}


def test_create_user_accepts_admin_role() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts/create_user.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout
    assert "admin" in out


def test_seed_includes_admin() -> None:
    spec = importlib.util.spec_from_file_location(
        "seed", ROOT / "scripts/seed_dispatch.py"
    )
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    assert ("admin@plongelven.io", "Admin", "admin") in seed.USERS
