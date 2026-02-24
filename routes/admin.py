"""
JWT-защищённые эндпоинты для панели оператора.

GET  /admin/dialogs                          — список диалогов (с пагинацией)
GET  /admin/dialogs/{session_id}             — полная история одного диалога
POST /admin/dialogs/{session_id}/reply       — ответ оператора
POST /admin/dialogs/{session_id}/close       — закрыть диалог
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Dialog, Message
from services.auth_service import decode_token

logger = logging.getLogger(__name__)

_bearer = HTTPBearer()


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def require_admin(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> str:
    """Проверить Bearer-токен. Вернуть username или выбросить HTTP 401."""
    try:
        username = decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username


# ---------------------------------------------------------------------------
# Router (все маршруты требуют аутентификации)
# ---------------------------------------------------------------------------

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class MessageOut(BaseModel):
    id: str
    role: str
    text: str
    created_at: str


class DialogSummary(BaseModel):
    session_id: str
    status: str
    user_name: str | None
    created_at: str
    updated_at: str
    last_message: str | None
    last_message_at: str | None


class DialogDetail(BaseModel):
    session_id: str
    status: str
    user_name: str | None
    created_at: str
    updated_at: str
    messages: list[MessageOut]


class ReplyRequest(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# GET /admin/dialogs
# ---------------------------------------------------------------------------

@router.get("/dialogs", response_model=list[DialogSummary])
async def list_dialogs(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Dialog).order_by(Dialog.updated_at.desc()).limit(limit).offset(offset)
    if status:
        query = query.where(Dialog.status == status)

    result = await db.execute(query)
    dialogs = result.scalars().all()

    if not dialogs:
        return []

    dialog_ids = [d.id for d in dialogs]

    # Один подзапрос для последнего сообщения каждого диалога (избегаем N+1)
    subq = (
        select(Message.dialog_id, func.max(Message.created_at).label("max_at"))
        .where(Message.dialog_id.in_(dialog_ids))
        .group_by(Message.dialog_id)
        .subquery()
    )
    last_msgs_result = await db.execute(
        select(Message).join(
            subq,
            (Message.dialog_id == subq.c.dialog_id)
            & (Message.created_at == subq.c.max_at),
        )
    )
    last_msgs = {m.dialog_id: m for m in last_msgs_result.scalars().all()}

    out = []
    for d in dialogs:
        lm = last_msgs.get(d.id)
        out.append(
            DialogSummary(
                session_id=d.id,
                status=d.status,
                user_name=d.user_name,
                created_at=d.created_at.isoformat(),
                updated_at=d.updated_at.isoformat(),
                last_message=lm.text if lm else None,
                last_message_at=lm.created_at.isoformat() if lm else None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# GET /admin/dialogs/{session_id}
# ---------------------------------------------------------------------------

@router.get("/dialogs/{session_id}", response_model=DialogDetail)
async def get_dialog(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Dialog).where(Dialog.id == session_id))
    dialog: Dialog | None = result.scalars().first()
    if dialog is None:
        raise HTTPException(status_code=404, detail="Dialog not found")

    msgs_result = await db.execute(
        select(Message).where(Message.dialog_id == session_id).order_by(Message.created_at)
    )
    messages = msgs_result.scalars().all()

    return DialogDetail(
        session_id=dialog.id,
        status=dialog.status,
        user_name=dialog.user_name,
        created_at=dialog.created_at.isoformat(),
        updated_at=dialog.updated_at.isoformat(),
        messages=[
            MessageOut(id=m.id, role=m.role, text=m.text, created_at=m.created_at.isoformat())
            for m in messages
        ],
    )


# ---------------------------------------------------------------------------
# POST /admin/dialogs/{session_id}/reply
# ---------------------------------------------------------------------------

@router.post("/dialogs/{session_id}/reply", response_model=MessageOut)
async def operator_reply(
    session_id: str,
    body: ReplyRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Dialog).where(Dialog.id == session_id))
    dialog: Dialog | None = result.scalars().first()
    if dialog is None:
        raise HTTPException(status_code=404, detail="Dialog not found")
    if dialog.status == "closed":
        raise HTTPException(status_code=409, detail="Dialog is closed")

    msg = Message(dialog_id=dialog.id, role="operator", text=body.message)
    db.add(msg)

    # Обновить updated_at диалога вручную (onupdate не срабатывает без flush по колонке)
    dialog.updated_at = datetime.now(timezone.utc)

    await db.flush()
    return MessageOut(
        id=msg.id,
        role=msg.role,
        text=msg.text,
        created_at=msg.created_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# POST /admin/dialogs/{session_id}/close
# ---------------------------------------------------------------------------

@router.post("/dialogs/{session_id}/close")
async def close_dialog(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Dialog).where(Dialog.id == session_id))
    dialog: Dialog | None = result.scalars().first()
    if dialog is None:
        raise HTTPException(status_code=404, detail="Dialog not found")

    dialog.status = "closed"
    dialog.updated_at = datetime.now(timezone.utc)
    return {"session_id": session_id, "status": "closed"}
