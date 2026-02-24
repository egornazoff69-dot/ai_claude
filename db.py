"""
Database bootstrap: async SQLAlchemy engine, session factory, Base, init_db().

Используется SQLite (файл dialogs.db в папке проекта).
Для переключения на Postgres достаточно изменить DATABASE_URL в .env:
  DATABASE_URL=postgresql+asyncpg://user:pass@host/dbname
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    """Единая декларативная база для всех ORM-моделей."""
    pass


def _get_engine():
    """Lazy-инициализация движка — читает settings при первом вызове."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        from main import settings  # lazy import — избегаем circular dep на уровне модуля
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            connect_args={"check_same_thread": False},  # нужно для SQLite
        )
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            expire_on_commit=False,  # важно для async: предотвращает lazy-load после закрытия сессии
            class_=AsyncSession,
        )
    return _session_factory


async def init_db() -> None:
    """
    Создать все таблицы (CREATE TABLE IF NOT EXISTS).
    Вызывается один раз при старте приложения через FastAPI lifespan.
    """
    import models  # noqa: PLC0415 — импорт здесь регистрирует модели в Base.metadata
    async with _get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency. Возвращает AsyncSession.
    Автоматически коммитит при успехе и откатывает при исключении.

    Использование:
        async def handler(db: AsyncSession = Depends(get_db)):
            ...
    """
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
