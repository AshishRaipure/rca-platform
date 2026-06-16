"""Async engine / session plumbing + ABAC scope application.

ABAC is enforced at the data layer by Postgres Row-Level Security. Before any query, the session
sets request-scoped GUCs (``app.user_id``, ``app.team_scope``, ``app.is_admin``) via
``set_config(..., is_local => true)``; the RLS policies (Phase 2 DDL / ``db/rls``) read these to
filter rows. Repositories additionally apply explicit team filters as defense-in-depth.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


async def apply_scope(session: AsyncSession, scope: dict[str, Any]) -> None:
    team_scope = ",".join(scope.get("team_ids") or [])
    await session.execute(
        text("SELECT set_config('app.user_id', :v, true)"),
        {"v": str(scope.get("user_id") or "")},
    )
    await session.execute(
        text("SELECT set_config('app.team_scope', :v, true)"), {"v": team_scope},
    )
    await session.execute(
        text("SELECT set_config('app.is_admin', :v, true)"),
        {"v": "true" if scope.get("is_admin") else "false"},
    )


class Database:
    def __init__(self, dsn: str, *, echo: bool = False) -> None:
        # dsn e.g. "postgresql+asyncpg://user:pass@host/db"
        self._engine = create_async_engine(dsn, echo=echo, pool_pre_ping=True)
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False,
                                                class_=AsyncSession)

    def new_session(self) -> AsyncSession:
        return self._sessionmaker()

    async def dispose(self) -> None:
        await self._engine.dispose()
