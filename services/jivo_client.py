"""
Jivo Bot API client.

Отправляет сообщения от бота в диалог Jivo и может передать чат оператору.

Токен бота (jivo_token) берётся из конфига тенанта:
  config/tenants/<tenant_id>.json → поле "jivo_token"

Jivo Bot API endpoint:
  POST https://bot.jivosite.com/webhooks/{token}/send

Документация Jivo Bot API:
  https://www.jivosite.com/api/

Режим mock (для разработки без реального Jivo):
  Если jivo_token пустой или равен "mock" — сообщения только логируются,
  никуда не отправляются.
"""

from __future__ import annotations

import logging
import uuid

import httpx

logger = logging.getLogger(__name__)

_JIVO_API_BASE = "https://bot.jivosite.com/webhooks"

# Переиспользуем один HTTP-клиент (пул соединений)
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client  # noqa: PLW0603
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


# ---------------------------------------------------------------------------
# Публичные функции
# ---------------------------------------------------------------------------


async def send_message(
    chat_id: str,
    client_id: str | None,
    text: str,
    token: str,
) -> None:
    """
    Отправить текстовое сообщение бота в диалог Jivo.

    Args:
        chat_id:   Идентификатор диалога из входящего webhook payload.
        client_id: Идентификатор посетителя из входящего webhook payload.
        text:      Текст ответа бота.
        token:     Токен бота Jivo (из поля "jivo_token" конфига тенанта).

    Вызывает: POST https://bot.jivosite.com/webhooks/{token}/send
    """
    if not token or token in ("...", "mock", ""):
        # Режим разработки: токен не задан, просто логируем
        logger.info("Jivo [mock] send_message | chat=%s | text=%.80s", chat_id, text)
        return

    payload = {
        "id": str(uuid.uuid4()),        # уникальный ID нашего ответа
        "client_id": client_id or chat_id,
        "chat_id": chat_id,
        "event": "bot_message",
        "messages": [
            {
                "type": "text",
                "body": text,           # Jivo использует "body", не "text"
            }
        ],
    }

    url = f"{_JIVO_API_BASE}/{token}/send"
    try:
        response = await _get_client().post(url, json=payload)
        response.raise_for_status()
        logger.debug(
            "Jivo send_message OK | chat=%s | status=%s",
            chat_id,
            response.status_code,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Jivo send_message HTTP error | chat=%s | %s — %s",
            chat_id,
            exc.response.status_code,
            exc.response.text,
        )
    except Exception as exc:
        logger.error("Jivo send_message failed | chat=%s | %s", chat_id, exc)


async def trigger_handoff(
    chat_id: str,
    client_id: str | None,
    token: str,
) -> None:
    """
    Завершить сессию бота и передать диалог живому оператору Jivo.

    Отправляет событие "bot_end" — после этого Jivo показывает диалог
    в очереди операторов.

    Args:
        chat_id:   Идентификатор диалога.
        client_id: Идентификатор посетителя.
        token:     Токен бота Jivo.

    Вызывает: POST https://bot.jivosite.com/webhooks/{token}/send
    """
    if not token or token in ("...", "mock", ""):
        logger.info("Jivo [mock] trigger_handoff | chat=%s", chat_id)
        return

    payload = {
        "id": str(uuid.uuid4()),
        "client_id": client_id or chat_id,
        "chat_id": chat_id,
        "event": "bot_end",             # Jivo: завершить бота, передать оператору
    }

    url = f"{_JIVO_API_BASE}/{token}/send"
    try:
        response = await _get_client().post(url, json=payload)
        response.raise_for_status()
        logger.info("Jivo handoff OK | chat=%s → передан оператору", chat_id)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Jivo trigger_handoff HTTP error | chat=%s | %s — %s",
            chat_id,
            exc.response.status_code,
            exc.response.text,
        )
    except Exception as exc:
        logger.error("Jivo trigger_handoff failed | chat=%s | %s", chat_id, exc)
