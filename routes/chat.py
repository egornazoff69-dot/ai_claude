"""
POST /chat              — отправить сообщение виджета (тенант из .env по умолчанию).
POST /chat/{tenant_id}  — отправить сообщение виджета с явным указанием тенанта.
GET  /chat/history      — получить историю диалога по session_id.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Dialog, Message
from services.llm_client import BaseLLMClient, create_llm_client, parse_llm_json, SYSTEM_PROMPT_RU
from utils.tenant_loader import get_active_tenant, load_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# ---------------------------------------------------------------------------
# LLM singleton
# ---------------------------------------------------------------------------

_llm_client: BaseLLMClient | None = None


def _get_llm() -> BaseLLMClient:
    global _llm_client  # noqa: PLW0603
    if _llm_client is None:
        _llm_client = create_llm_client()
    return _llm_client


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def _build_chat_system_prompt(tenant_id: str | None = None) -> str:
    """Формирует системный промпт с полным JSON тенанта для чат-виджета."""
    tenant = load_tenant(tenant_id) if tenant_id else get_active_tenant()
    return SYSTEM_PROMPT_RU + "\n\nДанные о кабинетах:\n" + json.dumps(tenant, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    user_name: str | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    text: str
    created_at: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    from_: str = "bot"

    class Config:
        populate_by_name = True

    def model_dump(self, **kwargs):  # type: ignore[override]
        d = super().model_dump(**kwargs)
        d["from"] = d.pop("from_")
        return d


class HistoryResponse(BaseModel):
    session_id: str
    status: str
    operator_mode: bool
    messages: list[MessageOut]


# ---------------------------------------------------------------------------
# Shared handler
# ---------------------------------------------------------------------------

async def _handle_chat(
    body: ChatRequest,
    db: AsyncSession,
    tenant_id: str | None = None,
) -> dict:
    """Общая логика для POST /chat и POST /chat/{tenant_id}."""
    # --- Получить или создать диалог ---
    if body.session_id:
        result = await db.execute(select(Dialog).where(Dialog.id == body.session_id))
        dialog: Dialog | None = result.scalars().first()
        if dialog is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if dialog.status == "closed":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dialog is closed")
    else:
        meta_data: dict = {}
        if tenant_id:
            meta_data["tenant_id"] = tenant_id
        dialog = Dialog(
            user_name=body.user_name,
            meta=json.dumps(meta_data) if meta_data else None,
        )
        db.add(dialog)
        await db.flush()

    # --- Сохранить сообщение пользователя ---
    user_msg = Message(dialog_id=dialog.id, role="user", text=body.message)
    db.add(user_msg)
    await db.flush()

    # --- Проверить operator_mode — если True, бот молчит ---
    if dialog.operator_mode:
        return {
            "session_id": dialog.id,
            "reply": "Оператор скоро вам ответит.",
            "from": "bot",
            "operator_mode": True,
        }

    # --- Загрузить историю для LLM ---
    hist_result = await db.execute(
        select(Message)
        .where(Message.dialog_id == dialog.id)
        .order_by(Message.created_at)
    )
    history = hist_result.scalars().all()

    llm_messages = []
    for msg in history:
        llm_role = "assistant" if msg.role in ("bot", "operator") else "user"
        llm_messages.append({"role": llm_role, "content": msg.text})

    # --- Вызов LLM ---
    try:
        system_prompt = _build_chat_system_prompt(tenant_id)
        raw = await _get_llm().ask(llm_messages, system_prompt=system_prompt)
        parsed = parse_llm_json(raw)
        reply_text: str = parsed.get("reply_text", raw)
    except Exception as exc:
        logger.error("LLM error: %s", exc)
        reply_text = "Извините, произошла техническая ошибка. Пожалуйста, попробуйте позже."

    # --- Сохранить ответ бота ---
    bot_msg = Message(dialog_id=dialog.id, role="bot", text=reply_text)
    db.add(bot_msg)
    # commit произойдёт автоматически в get_db()

    return {"session_id": dialog.id, "reply": reply_text, "from": "bot", "operator_mode": False}


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------

@router.post("", response_model=None)
async def send_message(body: ChatRequest, db: AsyncSession = Depends(get_db)):
    return await _handle_chat(body, db, tenant_id=None)


# ---------------------------------------------------------------------------
# POST /chat/{tenant_id}
# ---------------------------------------------------------------------------

@router.post("/{tenant_id}", response_model=None)
async def send_message_tenant(
    tenant_id: str,
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Отправить сообщение виджета с явным указанием тенанта.
    URL: POST /chat/{tenant_id}
    Зеркалирует паттерн /jivo/{tenant_id}.
    """
    try:
        load_tenant(tenant_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown tenant: {tenant_id}")
    return await _handle_chat(body, db, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# GET /chat/history
# ---------------------------------------------------------------------------

@router.get("/history", response_model=HistoryResponse)
async def get_history(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Dialog).where(Dialog.id == session_id))
    dialog: Dialog | None = result.scalars().first()
    if dialog is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    msgs_result = await db.execute(
        select(Message)
        .where(Message.dialog_id == session_id)
        .order_by(Message.created_at)
    )
    messages = msgs_result.scalars().all()

    return HistoryResponse(
        session_id=dialog.id,
        status=dialog.status,
        operator_mode=dialog.operator_mode,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                text=m.text,
                created_at=m.created_at.isoformat(),
            )
            for m in messages
        ],
    )
