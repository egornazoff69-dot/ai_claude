"""
Chatwoot REST API client.

Two public coroutines:
  send_message(conversation_id, content)  — send bot reply to a conversation
  assign_to_team(conversation_id, reason) — hand conversation off to a human agent

Errors are logged but never re-raised so a Chatwoot hiccup does not
break the overall message-handling flow.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _settings():
    """Lazy import to avoid circular dependency at module load time."""
    from main import settings  # noqa: PLC0415

    return settings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def send_message(
    conversation_id: int,
    content: str,
    *,
    private: bool = False,
) -> None:
    """
    Post a message to a Chatwoot conversation.

    Args:
        conversation_id: Chatwoot conversation ID.
        content: Text to send.
        private: If True the message is an internal note (not visible to customer).
    """
    cfg = _settings()
    url = (
        f"{cfg.chatwoot_base_url.rstrip('/')}"
        f"/api/v1/accounts/{cfg.chatwoot_account_id}"
        f"/conversations/{conversation_id}/messages"
    )
    payload: dict[str, Any] = {
        "content": content,
        "message_type": "outgoing",
        "private": private,
    }

    await _post(url, payload, cfg.chatwoot_api_token)


async def assign_to_team(
    conversation_id: int,
    reason: str | None = None,
) -> None:
    """
    Open the conversation for a human agent.

    Sets conversation status to "open" so it appears in the team queue.
    If a reason is provided it is added as a private note for context.
    """
    cfg = _settings()

    # Add private note with reason BEFORE changing status, so agents see why
    if reason:
        await send_message(
            conversation_id,
            f"[Причина передачи менеджеру]: {reason}",
            private=True,
        )

    url = (
        f"{cfg.chatwoot_base_url.rstrip('/')}"
        f"/api/v1/accounts/{cfg.chatwoot_account_id}"
        f"/conversations/{conversation_id}"
    )
    payload: dict[str, Any] = {"status": "open"}

    await _patch(url, payload, cfg.chatwoot_api_token)
    logger.info("Conversation %s assigned to human (status=open)", conversation_id)


# ---------------------------------------------------------------------------
# Internal HTTP helpers
# ---------------------------------------------------------------------------


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "api_access_token": token,
        "Content-Type": "application/json",
    }


async def _post(url: str, payload: dict[str, Any], token: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload, headers=_auth_headers(token))
            response.raise_for_status()
            logger.debug("Chatwoot POST %s → %s", url, response.status_code)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Chatwoot API error | POST %s | status=%s | body=%s",
            url,
            exc.response.status_code,
            exc.response.text[:200],
        )
    except httpx.RequestError as exc:
        logger.error("Chatwoot request failed | POST %s | %s", url, exc)


async def _patch(url: str, payload: dict[str, Any], token: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.patch(url, json=payload, headers=_auth_headers(token))
            response.raise_for_status()
            logger.debug("Chatwoot PATCH %s → %s", url, response.status_code)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Chatwoot API error | PATCH %s | status=%s | body=%s",
            url,
            exc.response.status_code,
            exc.response.text[:200],
        )
    except httpx.RequestError as exc:
        logger.error("Chatwoot request failed | PATCH %s | %s", url, exc)
