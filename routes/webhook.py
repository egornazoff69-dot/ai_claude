import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


class ChatwootContact(BaseModel):
    id: int | None = None
    name: str | None = None
    phone_number: str | None = None
    email: str | None = None


class ChatwootConversation(BaseModel):
    id: int


class ChatwootMessage(BaseModel):
    id: int | None = None
    content: str | None = None
    message_type: int | None = None  # 0=incoming, 1=outgoing, 2=activity
    sender: dict[str, Any] | None = None


class ChatwootWebhookPayload(BaseModel):
    """Payload sent by Chatwoot on message events."""

    event: str
    id: int | None = None
    content: str | None = None
    message_type: int | None = None
    conversation: dict[str, Any] | None = None
    contact: dict[str, Any] | None = None
    account: dict[str, Any] | None = None


@router.post("")
async def receive_webhook(request: Request) -> dict:
    """
    Receive incoming webhook from Chatwoot.

    Chatwoot fires this endpoint on every new message.
    We only process incoming messages (message_type == 0).
    """
    try:
        body = await request.json()
    except Exception as exc:
        logger.error("Failed to parse webhook body: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    payload = ChatwootWebhookPayload(**body)

    # Ignore non-message events and outgoing / activity messages
    if payload.event != "message_created":
        return {"status": "ignored", "reason": "not a message_created event"}

    if payload.message_type != 0:  # 0 = incoming from customer
        return {"status": "ignored", "reason": "not an incoming message"}

    content = (payload.content or "").strip()
    if not content:
        return {"status": "ignored", "reason": "empty message content"}

    conversation_id: int | None = None
    if payload.conversation:
        conversation_id = payload.conversation.get("id")

    contact_info: dict[str, Any] = payload.contact or {}

    logger.info(
        "Received message | conversation=%s | content=%.80s",
        conversation_id,
        content,
    )

    # Import here to avoid circular imports at module load time
    from services.dispatcher import handle_message  # noqa: PLC0415

    await handle_message(
        conversation_id=conversation_id,
        message=content,
        contact=contact_info,
    )

    return {"status": "ok"}
