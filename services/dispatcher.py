"""
Dispatcher — мозг агента.

Получает сообщение клиента + контекст диалога, вызывает LLM,
разбирает структурированный JSON-ответ и маршрутизирует действие:
  - plain reply       → отправить текст через reply_fn (или Chatwoot по умолчанию)
  - intent=BOOKING    → booking_logic → yclients_client
  - intent=HANDOFF    → передать оператору через handoff_fn (или Chatwoot по умолчанию)

Мульти-тенантность:
  - Передайте tenant_id, чтобы загрузить конкретный конфиг клиента.
  - Если tenant_id не передан — используется TENANT из .env (обратная совместимость).

Мульти-канальность (Chatwoot, Jivo и т.д.):
  - Передайте reply_fn и handoff_fn, чтобы использовать нужный канал.
  - Если не переданы — по умолчанию используется Chatwoot (исходное поведение).

Сессионная память (слоты + история) хранится в памяти процесса.
Для мульти-процессных деплоев замените на Redis.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from services.llm_client import SYSTEM_PROMPT_RU, Message, create_llm_client, parse_llm_json
from utils.tenant_loader import get_active_tenant, load_tenant

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory session store
# Ключ — conversation_id (int для Chatwoot, str для Jivo, None если неизвестен)
# ---------------------------------------------------------------------------

_sessions: dict[int | str | None, dict[str, Any]] = {}

_llm_client = None  # lazy singleton


def _get_llm():
    global _llm_client  # noqa: PLW0603
    if _llm_client is None:
        _llm_client = create_llm_client()
    return _llm_client


# ---------------------------------------------------------------------------
# Главная точка входа
# ---------------------------------------------------------------------------


async def handle_message(
    conversation_id: int | str | None,
    message: str,
    contact: dict[str, Any],
    tenant_id: str | None = None,
    reply_fn: Callable[[str], Awaitable[None]] | None = None,
    handoff_fn: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """
    Обработать одно входящее сообщение клиента от начала до конца.

    Args:
        conversation_id: Идентификатор диалога (int для Chatwoot, str для Jivo).
                         Используется как ключ сессии.
        message:         Текст сообщения от клиента.
        contact:         Данные контакта (name, phone_number, email).
        tenant_id:       ID тенанта для загрузки нужного конфига. Если None —
                         берётся из переменной TENANT в .env.
        reply_fn:        Async-функция для отправки ответа клиенту.
                         Если None — используется Chatwoot (исходное поведение).
        handoff_fn:      Async-функция для передачи диалога оператору.
                         Если None — используется Chatwoot (исходное поведение).
    """
    session = _sessions.setdefault(conversation_id, {"history": [], "slots": _empty_slots()})

    # Заполнить слоты из данных контакта (если они ещё не заполнены)
    _merge_contact_into_slots(session["slots"], contact)

    # Добавить сообщение клиента в историю
    session["history"].append({"role": "user", "content": message})

    # Загрузить конфиг тенанта
    tenant = load_tenant(tenant_id) if tenant_id else get_active_tenant()
    system_prompt = _build_system_prompt(tenant)

    # --- Локальные обёртки: выбирают канал (переданный или Chatwoot по умолчанию) ---

    async def send_reply(text: str) -> None:
        if reply_fn is not None:
            await reply_fn(text)
        else:
            await _chatwoot_send_reply(conversation_id, text)

    async def do_handoff(reason: str | None = None) -> None:
        if handoff_fn is not None:
            await handoff_fn()
        else:
            await _chatwoot_trigger_handoff(conversation_id, reason)

    # --- Вызов LLM ---
    try:
        raw = await _get_llm().ask(
            messages=session["history"],
            system_prompt=system_prompt,
        )
        parsed = parse_llm_json(raw)
    except (ValueError, Exception) as exc:
        logger.error("LLM error for conversation %s: %s", conversation_id, exc)
        await send_reply("Извините, произошла техническая ошибка. Соединяю с менеджером.")
        return

    reply_text: str = parsed.get("reply_text", "")
    intent: str = parsed.get("intent", "ASK_INFO")
    llm_slots: dict[str, Any] = parsed.get("slots", {})
    need_handoff: bool = parsed.get("need_handoff", False)
    handoff_reason: str | None = parsed.get("handoff_reason")

    # Добавить извлечённые слоты в сессию
    _merge_slots(session["slots"], llm_slots)

    # Добавить ответ ассистента в историю
    session["history"].append({"role": "assistant", "content": reply_text})

    logger.info(
        "Dispatcher | conv=%s | intent=%s | need_handoff=%s",
        conversation_id,
        intent,
        need_handoff,
    )

    # --- Маршрутизация по intent ---

    if need_handoff or intent == "HANDOFF_TO_HUMAN":
        await send_reply(reply_text)
        await do_handoff(handoff_reason)
        return

    if intent == "BOOKING":
        await _handle_booking(conversation_id, session["slots"], reply_text, send_reply, do_handoff)
        return

    # По умолчанию: просто отправить ответ
    await send_reply(reply_text)


# ---------------------------------------------------------------------------
# Слоты
# ---------------------------------------------------------------------------


def _empty_slots() -> dict[str, Any]:
    return {
        "date": None,
        "time": None,
        "people_count": None,
        "room_type": None,
        "room_id": None,
        "name": None,
        "phone": None,
        "email": None,
    }


def _merge_slots(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    """Перезаписываем только непустые значения, чтобы слоты накапливались по ходу диалога."""
    for key, value in incoming.items():
        if value is not None:
            existing[key] = value


def _merge_contact_into_slots(slots: dict[str, Any], contact: dict[str, Any]) -> None:
    if slots["name"] is None and contact.get("name"):
        slots["name"] = contact["name"]
    if slots["phone"] is None and contact.get("phone_number"):
        slots["phone"] = contact["phone_number"]
    if slots["email"] is None and contact.get("email"):
        slots["email"] = contact["email"]


# ---------------------------------------------------------------------------
# Построение system prompt
# ---------------------------------------------------------------------------


def _build_system_prompt(tenant: dict[str, Any]) -> str:
    rooms_summary = "\n".join(
        f"- {r['name']} (id: {r['id']}, вместимость: {r['capacity']} чел., "
        f"цена: {r['price_per_hour']} руб/час): {r['description']}"
        for r in tenant.get("rooms", [])
    )
    extra = tenant.get("agent_instructions", "")
    return (
        f"{SYSTEM_PROMPT_RU}\n\n"
        f"Информация о коворкинге «{tenant.get('name', '')}»:\n{rooms_summary}\n\n"
        f"Дополнительные инструкции: {extra}"
    )


# ---------------------------------------------------------------------------
# Chatwoot-специфичные реализации (используются по умолчанию, если reply_fn не передан)
# ---------------------------------------------------------------------------


async def _chatwoot_send_reply(conversation_id: int | str | None, text: str) -> None:
    """Отправить ответ в Chatwoot. Используется, если reply_fn не передан."""
    logger.info("REPLY to conv %s: %.120s", conversation_id, text)
    if conversation_id is not None:
        from services import chatwoot_client  # noqa: PLC0415

        await chatwoot_client.send_message(int(conversation_id), text)


async def _chatwoot_trigger_handoff(
    conversation_id: int | str | None, reason: str | None
) -> None:
    """Передать диалог оператору в Chatwoot. Используется, если handoff_fn не передан."""
    logger.info("HANDOFF conv %s, reason: %s", conversation_id, reason)
    if conversation_id is not None:
        from services import chatwoot_client  # noqa: PLC0415

        await chatwoot_client.assign_to_team(int(conversation_id), reason)


# ---------------------------------------------------------------------------
# Обработка бронирования
# ---------------------------------------------------------------------------


async def _handle_booking(
    conversation_id: int | str | None,
    slots: dict[str, Any],
    reply_text: str,
    send_reply: Callable[[str], Awaitable[None]],
    do_handoff: Callable[..., Awaitable[None]],
) -> None:
    """Проверить слоты и создать бронирование в Yclients, затем ответить клиенту."""
    from services import booking_logic  # noqa: PLC0415

    result = await booking_logic.validate_and_book(slots)

    if result["success"]:
        await send_reply(result["message"])
    else:
        customer_msg = reply_text or result["message"]
        await send_reply(customer_msg)

        # Эскалировать к человеку, если ошибка не в валидации слотов/данных
        if result["error"] not in ("missing_slots", "no_room", "outside_hours"):
            await do_handoff(result["error"])
