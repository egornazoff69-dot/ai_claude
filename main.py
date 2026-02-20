import uvicorn
from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    yclients_token: str = ""
    yclients_company_id: str = ""
    yclients_staff_id: str = ""

    chatwoot_base_url: str = "https://app.chatwoot.com"
    chatwoot_api_token: str = ""
    chatwoot_account_id: str = ""

    tenant: str = "coworking"
    port: int = 8000


settings = Settings()

app = FastAPI(title="Coworking AI Agent", version="1.0.0")

from routes.webhook import router as webhook_router  # noqa: E402

app.include_router(webhook_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "tenant": settings.tenant}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=True)
