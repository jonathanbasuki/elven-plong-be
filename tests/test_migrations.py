"""Tests for alembic migrations and related scripts."""

import importlib.util
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def test_admin_role_migration_is_the_single_head() -> None:
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    (head,) = script.get_heads()
    rev = script.get_revision(head)
    assert rev.down_revision == "f67711b4a0bb"
    source = Path(rev.path).read_text()
    assert '"id": 3' in source and '"name": "admin"' in source
    assert "DELETE FROM roles WHERE id = 3" in source


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
