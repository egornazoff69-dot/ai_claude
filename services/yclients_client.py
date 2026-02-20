"""
Yclients API client — booking creation only.

Public interface:
    create_booking(room_id, date, time, duration_hours, customer) -> dict

Dev / mock mode:
    Set YCLIENTS_TOKEN=mock (or leave it empty) in .env to get a fake
    success response without touching the real Yclients API.
    Useful for local testing when you don't have a Yclients account yet.

Yclients API reference: https://yclients.com/api/
Auth: Bearer token in Authorization header.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_YCLIENTS_BASE = "https://api.yclients.com"
_MOCK_TOKENS = {"", "mock", "mock_token"}


def _settings():
    from main import settings  # noqa: PLC0415

    return settings


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


async def create_booking(
    room_id: str,
    date: str,           # "YYYY-MM-DD"
    time: str,           # "HH:MM"
    duration_hours: int,
    customer: dict[str, Any],   # {name, phone, email}
) -> dict[str, Any]:
    """
    Create a booking record in Yclients.

    Returns:
        {"success": bool, "booking_id": str | None, "error": str | None}
    """
    cfg = _settings()

    if cfg.yclients_token.strip().lower() in _MOCK_TOKENS:
        logger.info(
            "Yclients MOCK mode | room=%s date=%s time=%s customer=%s",
            room_id,
            date,
            time,
            customer.get("name"),
        )
        return {"success": True, "booking_id": "MOCK-12345", "error": None}

    payload = _build_payload(cfg, room_id, date, time, duration_hours, customer)
    url = f"{_YCLIENTS_BASE}/v2/records/{cfg.yclients_company_id}"
    headers = {
        "Authorization": f"Bearer {cfg.yclients_token}",
        "Accept": "application/vnd.yclients.v2+json",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            booking_id = str(data.get("data", {}).get("id", ""))
            logger.info("Yclients booking created | id=%s", booking_id)
            return {"success": True, "booking_id": booking_id or None, "error": None}

    except httpx.HTTPStatusError as exc:
        error_body = exc.response.text[:300]
        logger.error(
            "Yclients API error | status=%s | body=%s",
            exc.response.status_code,
            error_body,
        )
        return {
            "success": False,
            "booking_id": None,
            "error": f"Yclients HTTP {exc.response.status_code}: {error_body}",
        }
    except httpx.RequestError as exc:
        logger.error("Yclients request failed: %s", exc)
        return {"success": False, "booking_id": None, "error": str(exc)}


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------


def _build_payload(
    cfg: Any,
    room_id: str,
    date: str,
    time: str,
    duration_hours: int,
    customer: dict[str, Any],
) -> dict[str, Any]:
    """Construct Yclients record creation payload."""
    dt_str = f"{date}T{time}:00"
    try:
        datetime.fromisoformat(dt_str)  # validate format
    except ValueError:
        dt_str = f"{date}T{time}:00"   # keep as-is, let Yclients validate

    return {
        "staff_id": int(cfg.yclients_staff_id) if cfg.yclients_staff_id else 1,
        "services": [],          # no specific service — room is the unit
        "appointments": [
            {
                "id": 1,
                "services": [],
                "staff_id": int(cfg.yclients_staff_id) if cfg.yclients_staff_id else 1,
                "datetime": dt_str,
                "duration": duration_hours * 60,   # Yclients uses minutes
                "client": {
                    "name": customer.get("name", ""),
                    "phone": customer.get("phone", ""),
                    "email": customer.get("email", ""),
                },
                "comment": f"room_id={room_id}",
            }
        ],
        "comment": f"Бронирование через AI-агент | room={room_id}",
    }
