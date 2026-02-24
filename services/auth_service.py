"""
Утилиты аутентификации: bcrypt-хеширование паролей и JWT-токены.

Используем bcrypt напрямую (passlib несовместим с bcrypt>=4.x).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt  # noqa: F401  (JWTError re-exported для импорта в других модулях)

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_DAYS = 7


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(subject: str) -> str:
    from main import settings  # lazy import — избегаем circular dep на уровне модуля

    expire = datetime.now(timezone.utc) + timedelta(days=_TOKEN_EXPIRE_DAYS)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> str:
    """Вернуть username (sub). Выбросить JWTError если токен невалиден или истёк."""
    from main import settings  # lazy import

    data = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    sub: str | None = data.get("sub")
    if sub is None:
        raise JWTError("missing sub claim")
    return sub
