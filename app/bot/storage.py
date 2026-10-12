"""FSM-хранилище aiogram 3 в таблице `fsm_states`.

Состояние сценария переживает перезапуск процесса и пересоздание диспетчера.
Данные (`state.update_data(...)`) хранятся в JSON — класть туда только сериализуемые
значения: строки, числа, bool, None, списки и словари из них (даты — строкой ISO).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, DefaultKeyBuilder, KeyBuilder, StateType, StorageKey
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import FsmState, utcnow


class DbStorage(BaseStorage):
    """`BaseStorage` поверх таблицы `fsm_states` (Postgres в проде, SQLite в тестах).

    `sessionmaker=None` — брать фабрику из `app.db` при каждом обращении, поэтому
    хранилище продолжает работать после подмены движка в тестах.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        key_builder: KeyBuilder | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self.key_builder = key_builder or DefaultKeyBuilder(with_bot_id=True, with_destiny=True)

    def _sm(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is not None:
            return self._sessionmaker
        from app.db import get_sessionmaker

        return get_sessionmaker()

    def _key(self, key: StorageKey) -> str:
        return self.key_builder.build(key)

    async def _upsert(self, key: StorageKey, values: dict[str, Any]) -> None:
        row_key = self._key(key)
        async with self._sm()() as session:
            dialect = session.get_bind().dialect.name
            insert = pg_insert if dialect == "postgresql" else sqlite_insert
            now = utcnow()
            stmt = insert(FsmState).values(key=row_key, updated_at=now, **{"data": {}, **values})
            stmt = stmt.on_conflict_do_update(index_elements=[FsmState.key], set_={**values, "updated_at": now})
            await session.execute(stmt)
            await session.commit()

    async def _get(self, key: StorageKey) -> FsmState | None:
        async with self._sm()() as session:
            return await session.scalar(select(FsmState).where(FsmState.key == self._key(key)))

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await self._upsert(key, {"state": value})
        if value is None:
            await self._cleanup(key)

    async def get_state(self, key: StorageKey) -> str | None:
        row = await self._get(key)
        return row.state if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        if not isinstance(data, Mapping):
            raise TypeError(f"Data must be a dict or dict-like object, got {type(data).__name__}")
        await self._upsert(key, {"data": dict(data)})
        if not data:
            await self._cleanup(key)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        row = await self._get(key)
        return dict(row.data or {}) if row else {}

    async def _cleanup(self, key: StorageKey) -> None:
        """Пустая строка (нет ни состояния, ни данных) не хранится."""
        async with self._sm()() as session:
            row = await session.scalar(select(FsmState).where(FsmState.key == self._key(key)))
            if row is not None and row.state is None and not row.data:
                await session.execute(delete(FsmState).where(FsmState.key == row.key))
                await session.commit()

    async def close(self) -> None:
        return None
