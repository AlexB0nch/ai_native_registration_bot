"""Подключение к базе: async engine, фабрика сессий, контекст транзакции.

Обычное использование в хендлерах и сервисах:

    async with session_scope() as session:
        person = await get_or_create_by_telegram(session, message.from_user, source)

`session_scope()` коммитит при выходе без исключения и откатывает при исключении.
В FastAPI — зависимость `get_session()`. Тесты подменяют движок через `set_engine()`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def make_engine(url: str | None = None) -> AsyncEngine:
    url = url or get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {}
    return create_async_engine(url, **kwargs)


def set_engine(engine: AsyncEngine | None) -> None:
    """Задать движок (тесты) или сбросить (`None` → создать из DATABASE_URL при следующем вызове)."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False) if engine is not None else None


def get_engine() -> AsyncEngine:
    if _engine is None:
        set_engine(make_engine())
    return _engine  # type: ignore[return-value]


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    return _sessionmaker  # type: ignore[return-value]


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Сессия-транзакция: commit при успехе, rollback при исключении."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: `session: AsyncSession = Depends(get_session)`."""
    async with session_scope() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
