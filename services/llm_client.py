"""
LLM abstraction layer.

A single public method: ask(system_prompt, messages) -> str.
Swap providers by changing LLM_BASE_URL / LLM_API_KEY / LLM_MODEL in .env —
no other code needs to change.

Supported providers (all via OpenAI-compatible API):
- OpenAI (gpt-4o, gpt-4o-mini, …)
- Any OpenAI-compatible endpoint (GigaChat, Together, self-hosted via LiteLLM, …)
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Message = dict[str, str]  # {"role": "user" | "assistant" | "system", "content": "…"}

# ---------------------------------------------------------------------------
# LLM response schema (strict JSON the LLM must return)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_RU = """Ты — AI-консультант коворкинга. Твоя задача: помочь клиенту выбрать и забронировать подходящее пространство.

Правила работы:
1. Общайся вежливо и по делу, без лишней воды.
2. Уточняй детали по одному вопросу за раз, не засыпай клиента сразу несколькими вопросами.
3. Для бронирования тебе нужны: имя, телефон, дата, время начала, количество человек и тип кабинета.
4. Если клиент агрессивен, настаивает на скидке сверх прайса или вопрос нестандартный — передавай менеджеру (HANDOFF_TO_HUMAN).
5. Предлагай конкретные кабинеты и их преимущества, основываясь на потребностях клиента.

ВАЖНО: всегда отвечай ТОЛЬКО валидным JSON строго в следующем формате (без markdown, без пояснений вне JSON):

{
  "reply_text": "<текст ответа клиенту>",
  "intent": "<ASK_INFO | CHOOSE_ROOM | BOOKING | HANDOFF_TO_HUMAN>",
  "slots": {
    "date": "<YYYY-MM-DD или null>",
    "time": "<HH:MM или null>",
    "people_count": <число или null>,
    "room_type": "<тип кабинета или null>",
    "room_id": "<идентификатор кабинета или null>",
    "name": "<имя клиента или null>",
    "phone": "<номер телефона или null>",
    "email": "<email или null>"
  },
  "need_handoff": <true | false>,
  "handoff_reason": "<причина передачи или null>"
}

Интенты:
- ASK_INFO — клиент спрашивает об условиях, ценах, услугах
- CHOOSE_ROOM — клиент выбирает тип пространства
- BOOKING — все слоты собраны, готовим бронирование
- HANDOFF_TO_HUMAN — нужен живой менеджер
"""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseLLMClient(ABC):
    """Contract: one method, always returns a raw string from the model."""

    @abstractmethod
    async def ask(self, messages: list[Message], system_prompt: str = SYSTEM_PROMPT_RU) -> str:
        """
        Send a conversation to the LLM and return its reply as a string.

        Args:
            messages: Chat history in OpenAI format (role/content dicts).
            system_prompt: Instruction injected as the first system message.

        Returns:
            The raw text response from the model (expected to be JSON).
        """


# ---------------------------------------------------------------------------
# OpenAI-compatible implementation
# ---------------------------------------------------------------------------


class OpenAILLMClient(BaseLLMClient):
    """
    Works with any OpenAI-compatible endpoint.

    Configure via .env:
        LLM_BASE_URL  — e.g. https://api.openai.com/v1 or http://localhost:11434/v1
        LLM_API_KEY   — API key (use "ollama" or "none" for local models)
        LLM_MODEL     — model name, e.g. gpt-4o-mini
    """

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._model = model
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def ask(self, messages: list[Message], system_prompt: str = SYSTEM_PROMPT_RU) -> str:
        full_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *messages,
        ]

        logger.debug("LLM request | model=%s | messages=%d", self._model, len(full_messages))

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=full_messages,  # type: ignore[arg-type]
            temperature=0.3,
            response_format={"type": "json_object"},  # enforce JSON output where supported
        )

        content = response.choices[0].message.content or ""
        logger.debug("LLM response | %.200s", content)
        return content


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_llm_client() -> BaseLLMClient:
    """
    Build the LLM client from environment settings.
    Import and call this once at startup (e.g. in main.py or dispatcher.py).
    """
    from main import settings  # lazy import to avoid circular deps at module level

    return OpenAILLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_llm_json(raw: str) -> dict[str, Any]:
    """
    Parse the JSON string returned by the LLM.

    Raises ValueError with a descriptive message if parsing fails,
    so the caller (dispatcher) can handle it gracefully.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned non-JSON content: {raw[:200]}") from exc

    required_keys = {"reply_text", "intent", "slots", "need_handoff"}
    missing = required_keys - data.keys()
    if missing:
        raise ValueError(f"LLM JSON missing required keys: {missing}")

    return data
