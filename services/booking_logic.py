"""
Booking validation and orchestration.

Public interface:
    validate_and_book(slots) -> dict

Internal helpers (also importable for unit testing):
    validate_slots(slots, required) -> (bool, list[str])
    select_room(slots, rooms)       -> dict | None
    validate_working_hours(date, time, working_hours) -> (bool, str)
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dt_time
from typing import Any

logger = logging.getLogger(__name__)

# Day-of-week names matching coworking.json keys (Python weekday: 0=Monday)
_WEEKDAYS = [
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
]


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def validate_slots(slots: dict[str, Any], required: list[str]) -> tuple[bool, list[str]]:
    """
    Check that all required slot fields are filled (non-null, non-empty).

    Returns:
        (True, []) if all required fields are present.
        (False, [list of missing field names]) otherwise.
    """
    missing = [field for field in required if not slots.get(field)]
    return (len(missing) == 0, missing)


def select_room(slots: dict[str, Any], rooms: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Find the best matching room from the tenant config.

    Priority:
    1. Exact match by room_id if provided.
    2. Match by room_type + people_count (smallest room that fits).

    Returns the room dict or None if no suitable room found.
    """
    room_id: str | None = slots.get("room_id")
    room_type: str | None = slots.get("room_type")
    people_count: int | None = slots.get("people_count")

    # 1. Exact match by ID
    if room_id:
        for room in rooms:
            if room["id"] == room_id:
                return room

    # 2. Match by type + capacity
    candidates = rooms
    if room_type:
        candidates = [r for r in candidates if r.get("type") == room_type]
    if people_count:
        candidates = [r for r in candidates if r.get("capacity", 0) >= people_count]

    if not candidates:
        return None

    # Return smallest fitting room (most efficient use of space)
    return min(candidates, key=lambda r: r.get("capacity", 999))


def validate_working_hours(
    date: str,
    time: str,
    working_hours: dict[str, Any],
) -> tuple[bool, str]:
    """
    Check that the requested date+time falls within the tenant's working hours.

    Args:
        date: "YYYY-MM-DD"
        time: "HH:MM"
        working_hours: dict from coworking.json (keys: monday…sunday)

    Returns:
        (True, "") if valid.
        (False, human-readable error string) if outside working hours.
    """
    try:
        dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return False, f"Неверный формат даты/времени: {date} {time}"

    weekday_key = _WEEKDAYS[dt.weekday()]
    hours = working_hours.get(weekday_key)

    if not hours:
        return False, f"В этот день недели ({weekday_key}) коворкинг не работает."

    def _parse_t(s: str) -> dt_time:
        h, m = map(int, s.split(":"))
        return dt_time(h, m)

    open_t = _parse_t(hours["open"])
    close_t = _parse_t(hours["close"])
    requested_t = dt.time()

    if not (open_t <= requested_t < close_t):
        return (
            False,
            f"Время {time} вне рабочих часов ({hours['open']}–{hours['close']} в {weekday_key}).",
        )

    return True, ""


# ---------------------------------------------------------------------------
# Main orchestration function
# ---------------------------------------------------------------------------


async def validate_and_book(slots: dict[str, Any]) -> dict[str, Any]:
    """
    Validate all booking slots and create a record in Yclients.

    Returns:
        {
            "success": bool,
            "booking_id": str | None,
            "error": str | None,
            "message": str,   # human-readable Russian message for the customer
        }
    """
    from services import yclients_client  # noqa: PLC0415
    from utils.tenant_loader import get_active_tenant  # noqa: PLC0415

    tenant = get_active_tenant()
    booking_rules: dict[str, Any] = tenant.get("booking_rules", {})
    required: list[str] = booking_rules.get(
        "required_slots",
        ["name", "phone", "date", "time", "people_count", "room_id"],
    )
    rooms: list[dict[str, Any]] = tenant.get("rooms", [])
    working_hours: dict[str, Any] = tenant.get("working_hours", {})

    # 1. Slot completeness check
    ok, missing = validate_slots(slots, required)
    if not ok:
        field_labels = {
            "name": "имя", "phone": "телефон", "date": "дату",
            "time": "время", "people_count": "количество человек", "room_id": "кабинет",
        }
        missing_ru = ", ".join(field_labels.get(f, f) for f in missing)
        msg = f"Для завершения бронирования мне нужно уточнить: {missing_ru}."
        logger.info("Booking validation failed | missing=%s", missing)
        return {"success": False, "booking_id": None, "error": "missing_slots", "message": msg}

    # 2. Room selection
    room = select_room(slots, rooms)
    if room is None:
        msg = "К сожалению, подходящего кабинета не нашлось. Уточните тип помещения или количество человек."
        logger.info("Booking failed | no matching room | slots=%s", slots)
        return {"success": False, "booking_id": None, "error": "no_room", "message": msg}

    # Ensure room_id is stored in slots for yclients
    slots["room_id"] = room["id"]

    # 3. Working hours check
    hours_ok, hours_error = validate_working_hours(
        slots["date"], slots["time"], working_hours
    )
    if not hours_ok:
        logger.info("Booking failed | outside hours | %s", hours_error)
        return {
            "success": False,
            "booking_id": None,
            "error": "outside_hours",
            "message": hours_error,
        }

    # 4. Create booking in Yclients
    customer = {
        "name": slots.get("name", ""),
        "phone": slots.get("phone", ""),
        "email": slots.get("email", ""),
    }
    duration_hours: int = booking_rules.get("min_duration_hours", 1)

    result = await yclients_client.create_booking(
        room_id=room["id"],
        date=slots["date"],
        time=slots["time"],
        duration_hours=duration_hours,
        customer=customer,
    )

    if result["success"]:
        booking_id = result["booking_id"]
        msg = (
            f"Готово! Ваше бронирование подтверждено.\n"
            f"Кабинет: {room['name']}\n"
            f"Дата: {slots['date']} в {slots['time']}\n"
            f"Номер брони: {booking_id}\n"
            f"Ждём вас! Если понадобится изменить бронь — напишите нам."
        )
        logger.info("Booking created | room=%s | id=%s", room["id"], booking_id)
        return {"success": True, "booking_id": booking_id, "error": None, "message": msg}
    else:
        msg = "Не удалось создать бронирование из-за технической ошибки. Соединяю с менеджером."
        logger.error("Yclients booking failed | error=%s", result["error"])
        return {
            "success": False,
            "booking_id": None,
            "error": result["error"],
            "message": msg,
        }
