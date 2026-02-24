"""
CLI-утилита для создания администратора.

Использование:
    python create_admin.py <username> <password>

Пример:
    python create_admin.py admin P@ssword
"""
from __future__ import annotations

import asyncio
import sys


async def main(username: str, password: str) -> None:
    if not username:
        print("Ошибка: имя пользователя не может быть пустым.")
        sys.exit(1)
    if len(password) < 6:
        print("Ошибка: пароль должен содержать не менее 6 символов.")
        sys.exit(1)

    from db import init_db, get_db
    from models import AdminUser
    from services.auth_service import hash_password
    from sqlalchemy import select

    await init_db()

    async for db in get_db():
        result = await db.execute(select(AdminUser).where(AdminUser.username == username))
        existing = result.scalars().first()
        if existing:
            print(f"Ошибка: пользователь '{username}' уже существует.")
            sys.exit(1)

        user = AdminUser(username=username, password_hash=hash_password(password))
        db.add(user)
        # commit произойдёт автоматически в get_db()

    print(f"Администратор '{username}' успешно создан.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Использование: python {sys.argv[0]} <username> <password>")
        sys.exit(1)

    asyncio.run(main(sys.argv[1], sys.argv[2]))
