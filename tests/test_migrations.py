"""Миграции: upgrade → downgrade → upgrade на SQLite и совпадение схемы с моделями.

Синхронные тесты: alembic env.py сам запускает event loop.
На Postgres то же самое проверяет CI (шаг «Migrations»).
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from app.models import Base

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TABLES = {"people", "events", "registrations", "deliveries", "questions", "fsm_states"}


def _config(db_path: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    cfg.attributes["configure_logger"] = False
    return cfg


def _tables(db_path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "migrations.db"
    cfg = _config(db_path)
    command.upgrade(cfg, "head")
    assert _tables(db_path) == EXPECTED_TABLES
    command.downgrade(cfg, "base")
    assert _tables(db_path) == set()
    command.upgrade(cfg, "head")
    assert _tables(db_path) == EXPECTED_TABLES


def test_models_match_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "compare.db"
    command.upgrade(_config(db_path), "head")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert diff == [], f"модели и миграции расходятся: {diff}"
