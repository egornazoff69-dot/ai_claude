"""
Точка входа FastAPI-приложения.

Доступные эндпоинты:
  GET  /health            — проверка работоспособности сервера
  POST /webhook           — входящие события от Chatwoot
  POST /jivo/{tenant_id}  — входящие сообщения от Jivo Bot API

Как запустить:
  python main.py
  — или —
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Какой URL давать в Jivo:
  https://ВАШ_ДОМЕН/jivo/coworking   (замените "coworking" на ID вашего тенанта)
  Этот URL нужно вставить в настройки бота в кабинете Jivo:
    jivosite.com → Управление → Боты → Webhook URL
"""

import uvicorn
from fastapi import FastAPI
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


settings = Settings()

app = FastAPI(
    title="Coworking AI Agent",
    version="2.0.0",
    description=(
        "ИИ-агент для коворкингов. "
        "Поддерживает Chatwoot и Jivo, мульти-тенантность."
    ),
)

# --- Роутеры ---

from routes.webhook import router as chatwoot_router  # noqa: E402
from routes.jivo_webhook import router as jivo_router  # noqa: E402

app.include_router(chatwoot_router)   # POST /webhook  — Chatwoot
app.include_router(jivo_router)       # POST /jivo/{tenant_id}  — Jivo


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
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=True)
