"""
Dispatcher — the single brain of the agent.

Receives a customer message + conversation context, calls the LLM,
parses the structured JSON response, and routes to the appropriate action:
  - plain reply  → send text back via Chatwoot API
  - BOOKING      → booking_logic → yclients_client
  - HANDOFF      → mark conversation in Chatwoot for a human agent

Session memory (slots + history) is kept in a plain in-process dict keyed
by conversation_id.  Replace with Redis for multi-process deployments.

NOTE: booking_logic and yclients_client are stubbed in this first iteration.
They will be implemented in modules 3-4.
"""

from __future__ import annotations

import logging
from typing import Any

from services.llm_client import SYSTEM_PROMPT_RU, Message, create_llm_client, parse_llm_json
from utils.tenant_loader import get_active_tenant

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory session store  {conversation_id: {"history": [...], "slots": {...}}}
# ---------------------------------------------------------------------------

_sessions: dict[int | None, dict[str, Any]] = {}

_llm_client = None  # lazy singleton


def _get_llm():
    global _llm_client  # noqa: PLW0603
    if _llm_client is None:
        _llm_client = create_llm_client()
    return _llm_client


# ---------------------------------------------------------------------------
# Main entry point called by webhook.py
# ---------------------------------------------------------------------------


async def handle_message(
    conversation_id: int | None,
    message: str,
    contact: dict[str, Any],
) -> None:
    """
    Process one incoming customer message end-to-end.

    Args:
        conversation_id: Chatwoot conversation identifier (used as session key).
        message: Raw text from the customer.
        contact: Chatwoot contact dict (may contain name, phone, email).
    """
    session = _sessions.setdefault(conversation_id, {"history": [], "slots": _empty_slots()})

    # Pre-fill slots from Chatwoot contact data if not yet set
    _merge_contact_into_slots(session["slots"], contact)

    # Append customer turn to history
    session["history"].append({"role": "user", "content": message})

    tenant = get_active_tenant()
    system_prompt = _build_system_prompt(tenant)

    try:
        raw = await _get_llm().ask(
            messages=session["history"],
            system_prompt=system_prompt,
        )
        parsed = parse_llm_json(raw)
    except (ValueError, Exception) as exc:
        logger.error("LLM error for conversation %s: %s", conversation_id, exc)
        # Fallback: apologise and request human takeover
        await _send_reply(conversation_id, "Извините, произошла техническая ошибка. Соединяю с менеджером.")
        return

    reply_text: str = parsed.get("reply_text", "")
    intent: str = parsed.get("intent", "ASK_INFO")
    llm_slots: dict[str, Any] = parsed.get("slots", {})
    need_handoff: bool = parsed.get("need_handoff", False)
    handoff_reason: str | None = parsed.get("handoff_reason")

    # Merge newly extracted slots into session
    _merge_slots(session["slots"], llm_slots)

    # Append assistant turn
    session["history"].append({"role": "assistant", "content": reply_text})

    logger.info(
        "Dispatcher | conv=%s | intent=%s | need_handoff=%s",
        conversation_id,
        intent,
        need_handoff,
    )

    # --- Route by intent ---

    if need_handoff or intent == "HANDOFF_TO_HUMAN":
        await _send_reply(conversation_id, reply_text)
        await _trigger_handoff(conversation_id, handoff_reason)
        return

    if intent == "BOOKING":
        await _handle_booking(conversation_id, session["slots"], reply_text)
        return

    # Default: send the reply text as-is
    await _send_reply(conversation_id, reply_text)


# ---------------------------------------------------------------------------
# Slot helpers
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
    """Overwrite only non-null incoming values so slots accumulate across turns."""
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
# System prompt builder
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
# Side-effect implementations
# ---------------------------------------------------------------------------


async def _send_reply(conversation_id: int | None, text: str) -> None:
    """Send reply back to Chatwoot."""
    logger.info("REPLY to conv %s: %.120s", conversation_id, text)
    if conversation_id is not None:
        from services import chatwoot_client  # noqa: PLC0415

        await chatwoot_client.send_message(conversation_id, text)


async def _trigger_handoff(conversation_id: int | None, reason: str | None) -> None:
    """Assign conversation to a human agent in Chatwoot."""
    logger.info("HANDOFF conv %s, reason: %s", conversation_id, reason)
    if conversation_id is not None:
        from services import chatwoot_client  # noqa: PLC0415

        await chatwoot_client.assign_to_team(conversation_id, reason)


async def _handle_booking(
    conversation_id: int | None,
    slots: dict[str, Any],
    reply_text: str,
) -> None:
    """Validate slots and create a booking in Yclients, then reply to the customer."""
    from services import booking_logic  # noqa: PLC0415

    result = await booking_logic.validate_and_book(slots)

    if result["success"]:
        await _send_reply(conversation_id, result["message"])
    else:
        # If LLM already composed a reply, prefer it; otherwise use logic's message
        customer_msg = reply_text or result["message"]
        await _send_reply(conversation_id, customer_msg)

        # Escalate to human if Yclients itself failed (not just missing slots)
        if result["error"] not in ("missing_slots", "no_room", "outside_hours"):
            await _trigger_handoff(conversation_id, reason=result["error"])
