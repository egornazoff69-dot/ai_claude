"""
Точка входа FastAPI-приложения.

Доступные эндпоинты:
  GET  /health                              — проверка работоспособности сервера
  POST /webhook                             — входящие события от Chatwoot
  POST /jivo/{tenant_id}                    — входящие сообщения от Jivo Bot API
  POST /chat                                — чат-виджет: отправить сообщение
  POST /chat/{tenant_id}                    — чат-виджет: мультитенант
  GET  /chat/history                        — чат-виджет: история диалога
  POST /auth/login                          — получить JWT-токен оператора
  GET  /admin/dialogs                       — список диалогов (для операторов)
  GET  /admin/dialogs/{session_id}          — история одного диалога
  POST /admin/dialogs/{session_id}/reply    — ответ оператора
  POST /admin/dialogs/{session_id}/close    — закрыть диалог
  POST /admin/dialogs/{session_id}/takeover — оператор берёт диалог
  POST /admin/dialogs/{session_id}/release  — передать диалог боту
  GET  /static/widget.js                    — встраиваемый виджет
  GET  /static/admin/index.html             — панель оператора

Как запустить:
  python main.py
  — или —
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Какой URL давать в Jivo:
  https://ВАШ_ДОМЕН/jivo/coworking   (замените "coworking" на ID вашего тенанта)
  Этот URL нужно вставить в настройки бота в кабинете Jivo:
    jivosite.com → Управление → Боты → Webhook URL
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # LLM (OpenAI или совместимый провайдер)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    # Yclients (система бронирования)
    yclients_token: str = ""
    yclients_company_id: str = ""
    yclients_staff_id: str = ""

    # Chatwoot (мессенджер — опционально, если используете Chatwoot)
    chatwoot_base_url: str = "https://app.chatwoot.com"
    chatwoot_api_token: str = ""
    chatwoot_account_id: str = ""

    # Тенант по умолчанию (для Chatwoot-интеграции и /health)
    # Для Jivo тенант определяется из URL: /jivo/{tenant_id}
    tenant: str = "coworking"

    port: int = 8000

    # --- Новые поля: БД, JWT, CORS ---
    jwt_secret: str = "change-me"
    database_url: str = "sqlite+aiosqlite:///./dialogs.db"
    cors_origins: str = "*"  # через запятую: "https://site1.ru,https://site2.ru"


settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from db import init_db
    await init_db()
    yield


app = FastAPI(
    title="Coworking AI Agent",
    version="3.0.0",
    description=(
        "ИИ-агент для коворкингов. "
        "Поддерживает Chatwoot, Jivo, чат-виджет и панель оператора."
    ),
    lifespan=lifespan,
)

# --- CORS ---

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Роутеры ---

from routes.webhook import router as chatwoot_router       # noqa: E402
from routes.jivo_webhook import router as jivo_router      # noqa: E402
from routes.auth import router as auth_router              # noqa: E402
from routes.chat import router as chat_router              # noqa: E402
from routes.admin import router as admin_router            # noqa: E402

app.include_router(chatwoot_router)   # POST /webhook
app.include_router(jivo_router)       # POST /jivo/{tenant_id}
app.include_router(auth_router)       # POST /auth/login
app.include_router(chat_router)       # POST /chat, POST /chat/{tenant_id}, GET /chat/history
app.include_router(admin_router)      # GET/POST /admin/...

# --- Статические файлы (виджет и панель оператора) ---

import os as _os
_static_dir = _os.path.join(_os.path.dirname(__file__), "static")
if _os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# --- Healthcheck ---

@app.get("/health")
async def health() -> dict:
    """
    Проверка работоспособности.
    Также показывает, какие тенанты доступны.
    """
    from pathlib import Path

    tenants_dir = Path(__file__).parent / "config" / "tenants"
    available_tenants = [p.stem for p in tenants_dir.glob("*.json")]

    return {
        "status": "ok",
        "default_tenant": settings.tenant,
        "available_tenants": sorted(available_tenants),
        "jivo_endpoint": "/jivo/{tenant_id}",
        "chatwoot_endpoint": "/webhook",
        "chat_endpoint": "/chat",
        "admin_endpoint": "/admin/dialogs",
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=True)
