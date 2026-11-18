"""Shared dependencies handed from server.main.create_app to every v2 route group."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Ctx:
    db_session: Callable
    auth_session: Callable
    get_current: Callable
    event: Callable
    tenant_public: Callable
    athlete_for: Callable
    case_for: Callable
    rls: bool
    hosted_rls: bool
    mode: str
    extra: dict[str, Any]


def is_pg(db) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def require_admin(user) -> None:
    from fastapi import HTTPException
    if not user.is_admin:
        raise HTTPException(403, "Administrator required")
