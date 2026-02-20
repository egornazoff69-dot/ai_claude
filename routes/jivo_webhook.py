"""
Jivo Bot API webhook handler.

Как это работает:
  1. Каждый клиент (тенант) получает свой URL-эндпоинт:
       POST https://your-server.com/jivo/<tenant_id>
     Например: https://your-server.com/jivo/coworking

  2. В настройках бота Jivo (jivosite.com → Управление → Боты) укажите этот URL
     как "Webhook URL".

  3. Когда посетитель сайта пишет в чат, Jivo шлёт POST на этот URL.
     Мы отвечаем "ok" мгновенно, а ответ клиенту отправляем асинхронно
     через jivo_client.send_message() — это нужно, чтобы не превышать
     таймаут Jivo (5 секунд), пока LLM думает.

  4. Токен бота Jivo хранится в конфиге тенанта: config/tenants/<tenant_id>.json
     Поле: "jivo_token": "ваш-токен-из-кабинета-jivo"

Добавить нового клиента:
  - Создайте config/tenants/client_b.json (скопируйте coworking.json, поменяйте данные)
  - Добавьте в него "jivo_token": "токен-Jivo-этого-клиента"
  - В Jivo укажите URL: https://your-server.com/jivo/client_b
  - Всё.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from utils.tenant_loader import load_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jivo", tags=["jivo"])


# ---------------------------------------------------------------------------
# Pydantic models — структура payload от Jivo Bot API
# ---------------------------------------------------------------------------


class JivoSender(BaseModel):
    """Информация о посетителе, которую Jivo передаёт вместе с сообщением."""

    id: str | None = None
    name: str | None = None
    phone: str | None = None
    email: str | None = None


class JivoMessageNested(BaseModel):
    """Вложенный объект message (встречается в некоторых версиях Jivo API)."""

    type: str = "text"
    text: str | None = None   # текст в nested-формате
    body: str | None = None   # альтернативное поле


class JivoWebhookPayload(BaseModel):
    """
    Входящий event от Jivo Bot API.

    Jivo использует два слегка отличающихся формата (flat и nested).
    Мы принимаем оба — поля дублируются, лишние игнорируются Pydantic.
    """

    event: str                              # "client_message" | "client_new_chat" | ...
    id: str | None = None                   # уникальный ID события
    client_id: str | None = None            # идентификатор посетителя
    chat_id: str | None = None              # идентификатор диалога (ключ сессии)
    body: str | None = None                 # текст (flat-формат)
    type: str | None = None                 # тип сообщения (flat-формат)
    sender: JivoSender | None = None        # данные посетителя
    message: JivoMessageNested | None = None  # объект сообщения (nested-формат)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _extract_text(payload: JivoWebhookPayload) -> str:
    """
    Извлекает текст сообщения из payload любого формата Jivo.

    Jivo в разных версиях кладёт текст в разные поля:
      - Flat:   payload.body
      - Nested: payload.message.text  или  payload.message.body
    """
    if payload.body:
        return payload.body.strip()
    if payload.message:
        return (payload.message.text or payload.message.body or "").strip()
    return ""


def _extract_contact(payload: JivoWebhookPayload) -> dict[str, Any]:
    """
    Преобразует данные посетителя из формата Jivo в формат, который ожидает dispatcher.
    Dispatcher читает поля: name, phone_number, email.
    """
    sender = payload.sender or JivoSender()
    return {
        "name": sender.name,
        "phone_number": sender.phone,  # dispatcher использует именно "phone_number"
        "email": sender.email,
    }


# ---------------------------------------------------------------------------
# Эндпоинт
# ---------------------------------------------------------------------------


@router.post("/{tenant_id}")
async def receive_jivo_webhook(
    tenant_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Принимает входящее сообщение от Jivo Bot API.

    URL: POST /jivo/{tenant_id}
    Этот URL нужно вставить в настройки бота в кабинете Jivo.
    """
    # 1. Разобрать JSON
    try:
        body = await request.json()
    except Exception as exc:
        logger.error("Jivo webhook: не удалось разобрать JSON: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    payload = JivoWebhookPayload(**body)

    # 2. Обрабатываем только сообщения от клиента
    if payload.event.lower() not in ("client_message", "client_new_chat"):
        logger.debug("Jivo: пропускаем event=%s", payload.event)
        return {"status": "ignored", "reason": f"event={payload.event}"}

    # 3. Извлечь текст
    text = _extract_text(payload)
    if not text:
        return {"status": "ignored", "reason": "empty message"}

    # 4. Проверить, что тенант существует (упадёт с 404, если нет)
    try:
        load_tenant(tenant_id)
    except FileNotFoundError:
        logger.error("Jivo: неизвестный tenant_id=%s", tenant_id)
        raise HTTPException(status_code=404, detail=f"Unknown tenant: {tenant_id}")

    # chat_id — главный идентификатор диалога; client_id — посетителя
    chat_id: str = payload.chat_id or payload.client_id or "unknown"
    contact = _extract_contact(payload)

    logger.info(
        "Jivo: входящее сообщение | tenant=%s | chat_id=%s | text=%.80s",
        tenant_id,
        chat_id,
        text,
    )

    # 5. Обработка в фоне — LLM может думать несколько секунд,
    #    а Jivo ждёт ответ не больше 5 секунд, поэтому отвечаем "ok" сразу.
    background_tasks.add_task(
        _process_message,
        tenant_id=tenant_id,
        chat_id=chat_id,
        client_id=payload.client_id,
        text=text,
        contact=contact,
    )

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Фоновая задача: LLM + отправка ответа в Jivo
# ---------------------------------------------------------------------------


async def _process_message(
    tenant_id: str,
    chat_id: str,
    client_id: str | None,
    text: str,
    contact: dict[str, Any],
) -> None:
    """
    Запускается в фоне после того, как HTTP-ответ "ok" уже отправлен в Jivo.

    Шаги:
      1. Загружает конфиг тенанта (включая jivo_token).
      2. Вызывает dispatcher.handle_message() с колбэками для отправки ответа в Jivo.
      3. Dispatcher вызывает LLM, решает intent, вызывает booking_logic при необходимости.
      4. Ответ бота отправляется через jivo_client.send_message().
    """
    try:
        from services import jivo_client
        from services.dispatcher import handle_message

        tenant_config = load_tenant(tenant_id)
        # Токен бота Jivo хранится в конфиге тенанта (поле "jivo_token")
        jivo_token: str = tenant_config.get("jivo_token", "")

        # Колбэк для отправки текстового ответа в Jivo
        async def reply_fn(message_text: str) -> None:
            await jivo_client.send_message(
                chat_id=chat_id,
                client_id=client_id,
                text=message_text,
                token=jivo_token,
            )

        # Колбэк для передачи диалога живому оператору
        async def handoff_fn() -> None:
            await jivo_client.trigger_handoff(
                chat_id=chat_id,
                client_id=client_id,
                token=jivo_token,
            )

        await handle_message(
            conversation_id=chat_id,   # строка — диалог в Jivo идентифицируется chat_id
            message=text,
            contact=contact,
            tenant_id=tenant_id,       # загрузить нужный конфиг тенанта
            reply_fn=reply_fn,
            handoff_fn=handoff_fn,
        )

    except Exception as exc:
        # Логируем, но не пробрасываем — Jivo уже получил HTTP 200
        logger.error(
            "Jivo _process_message error | tenant=%s | chat=%s | %s",
            tenant_id,
            chat_id,
            exc,
            exc_info=True,
        )
