"""
Tenant config loader.

Reads config/tenants/<tenant_id>.json and returns it as a dict.
The result is cached in memory after the first load.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "tenants"


@lru_cache(maxsize=16)
def load_tenant(tenant_id: str) -> dict[str, Any]:
    """
    Load and cache the tenant configuration JSON.

    Args:
        tenant_id: Matches the filename, e.g. "coworking" -> coworking.json.

    Returns:
        Parsed config dict.

    Raises:
        FileNotFoundError: If no config file exists for this tenant.
        ValueError: If the file contains invalid JSON.
    """
    config_path = _CONFIG_DIR / f"{tenant_id}.json"

    if not config_path.exists():
        raise FileNotFoundError(
            f"Tenant config not found: {config_path}. "
            f"Available: {[p.stem for p in _CONFIG_DIR.glob('*.json')]}"
        )

    try:
        config: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in tenant config {config_path}: {exc}") from exc

    logger.info("Loaded tenant config: %s (%d rooms)", tenant_id, len(config.get("rooms", [])))
    return config


def get_active_tenant() -> dict[str, Any]:
    """
    Return the tenant config for the currently configured TENANT env var.
    Convenience wrapper used by dispatcher and booking_logic.
    """
    from main import settings  # lazy import to keep this module import-safe

    return load_tenant(settings.tenant)
