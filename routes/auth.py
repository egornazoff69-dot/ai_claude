"""
POST /auth/login — получить JWT-токен оператора.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import AdminUser
from services.auth_service import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Фиктивный хеш — lazy, чтобы не вычислять bcrypt при импорте модуля.
# Нужен для защиты от timing-атак (перебор имён пользователей).
_DUMMY_HASH: str | None = None


def _get_dummy_hash() -> str:
    global _DUMMY_HASH  # noqa: PLW0603
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy")
    return _DUMMY_HASH


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    result = await db.execute(
        select(AdminUser).where(AdminUser.username == body.username)
    )
    user: AdminUser | None = result.scalars().first()

    if user is None:
        # Timing-safe: всё равно выполняем verify, чтобы злоумышленник не мог
        # определить, существует ли пользователь по времени ответа.
        verify_password(body.password, _get_dummy_hash())
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(subject=user.username)
    return TokenResponse(access_token=token)
