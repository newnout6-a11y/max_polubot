import asyncio
import json
import logging
from typing import List

import httpx
from google import genai
from pydantic import BaseModel, Field, field_validator

from core.config import (
    AI_PROVIDER,
    AI_REQUEST_TIMEOUT_SECONDS,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_REASONING_EFFORT,
    DEEPSEEK_WIRE_API,
    DISABLE_RESPONSE_STORAGE,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OPENAI_REASONING_EFFORT,
    OPENAI_WIRE_API,
)

logger = logging.getLogger(__name__)

_gemini_client = None
_gemini_key = None


class Transaction(BaseModel):
    category: str = Field(description="Lowercase category or item name.")
    expense: int = Field(default=0, ge=0, description="Positive amount spent.")
    income: int = Field(default=0, ge=0, description="Positive amount earned.")

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("category cannot be empty")
        return normalized


class ExtractionResult(BaseModel):
    transactions: List[Transaction] = Field(default_factory=list)


def _setting(settings, key, default):
    return settings.get(key) if settings else default


def _provider(settings=None) -> str:
    return str(_setting(settings, "ai_provider", AI_PROVIDER)).strip().lower()


def _provider_config(settings=None):
    provider = _provider(settings)
    if provider == "openai":
        return {
            "provider": provider,
            "api_key": _setting(settings, "openai_api_key", OPENAI_API_KEY),
            "model": _setting(settings, "openai_model", OPENAI_MODEL),
            "base_url": _setting(settings, "openai_base_url", OPENAI_BASE_URL),
            "wire_api": _setting(settings, "openai_wire_api", OPENAI_WIRE_API),
            "reasoning_effort": _setting(
                settings,
                "openai_reasoning_effort",
                OPENAI_REASONING_EFFORT,
            ),
            "disable_response_storage": _setting(
                settings,
                "disable_response_storage",
                DISABLE_RESPONSE_STORAGE,
            ),
        }
    if provider == "deepseek":
        return {
            "provider": provider,
            "api_key": _setting(settings, "deepseek_api_key", DEEPSEEK_API_KEY),
            "model": _setting(settings, "deepseek_model", DEEPSEEK_MODEL),
            "base_url": _setting(settings, "deepseek_base_url", DEEPSEEK_BASE_URL),
            "wire_api": _setting(settings, "deepseek_wire_api", DEEPSEEK_WIRE_API),
            "reasoning_effort": _setting(
                settings,
                "deepseek_reasoning_effort",
                DEEPSEEK_REASONING_EFFORT,
            ),
            "disable_response_storage": False,
        }
    return {
        "provider": "gemini",
        "api_key": _setting(settings, "gemini_api_key", GEMINI_API_KEY),
        "model": _setting(settings, "gemini_model", GEMINI_MODEL),
        "base_url": "",
        "wire_api": "",
        "reasoning_effort": "",
        "disable_response_storage": False,
    }


def _get_gemini_client(api_key: str | None = None):
    global _gemini_client, _gemini_key

    if not api_key:
        return None
    if _gemini_client is None or _gemini_key != api_key:
        try:
            _gemini_client = genai.Client(api_key=api_key)
            _gemini_key = api_key
        except Exception as exc:
            logger.error("Failed to init Gemini client: %s", exc)
            _gemini_client = None
            _gemini_key = None
    return _gemini_client


def is_ai_available(settings=None) -> bool:
    config = _provider_config(settings)
    return bool(config["api_key"])


def _prompt(text: str) -> str:
    return f"""
You are a financial assistant reading messages from a team chat.
Extract financial transactions from the message.

Rules:
- Return JSON only: {{"transactions":[{{"category":"...", "expense":0, "income":0}}]}}
- Return an empty transactions list when there are no expenses or incomes.
- Negative numbers, "spent", "paid", "купил", "потратил", "минус" are expenses.
- Received money, "получил", "доход", "плюс", "зачислили" are incomes.
- Keep category short, lowercase and human-readable.
- Do not invent values that are not present in the message.

Message:
{json.dumps(text, ensure_ascii=False)}
"""


async def _parse_with_gemini(text: str, api_key: str, model: str) -> List[Transaction]:
    client = _get_gemini_client(api_key)
    if not client:
        raise RuntimeError("Gemini API client is not initialized.")

    loop = asyncio.get_running_loop()

    def run_sync():
        return client.models.generate_content(
            model=model,
            contents=_prompt(text),
            config={
                "response_mime_type": "application/json",
                "response_schema": ExtractionResult,
            },
        )

    response = await asyncio.wait_for(
        loop.run_in_executor(None, run_sync),
        timeout=AI_REQUEST_TIMEOUT_SECONDS,
    )

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, ExtractionResult):
        return parsed.transactions
    if isinstance(parsed, dict):
        return ExtractionResult(**parsed).transactions

    data = json.loads(response.text or "{}")
    return ExtractionResult(**data).transactions


async def _parse_with_openai_compatible(text: str, api_key: str, model: str, base_url: str):
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Extract financial transactions and return strict JSON.",
            },
            {"role": "user", "content": _prompt(text)},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    content = data["choices"][0]["message"]["content"]
    return ExtractionResult(**json.loads(content)).transactions


def _extract_response_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"]
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                return content["text"]
    raise RuntimeError("Responses API returned no output text")


async def _parse_with_responses_api(
    text: str,
    api_key: str,
    model: str,
    base_url: str,
    reasoning_effort: str,
    disable_response_storage: bool,
):
    url = base_url.rstrip("/") + "/responses"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": "Extract financial transactions and return strict JSON.",
            },
            {"role": "user", "content": _prompt(text)},
        ],
        "text": {"format": {"type": "json_object"}},
    }

    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    if disable_response_storage:
        payload["store"] = False

    async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    return ExtractionResult(**json.loads(_extract_response_text(data))).transactions


async def parse_financial_message(text: str, settings=None) -> List[Transaction]:
    """Parse a chat message into financial transactions."""
    if not text.strip():
        return []

    config = _provider_config(settings)
    if not config["api_key"]:
        raise RuntimeError(f"{config['provider']} API key is not configured.")

    try:
        if config["provider"] == "gemini":
            return await _parse_with_gemini(text, config["api_key"], config["model"])

        if config["wire_api"] == "responses":
            return await _parse_with_responses_api(
                text,
                config["api_key"],
                config["model"],
                config["base_url"],
                config["reasoning_effort"],
                config["disable_response_storage"],
            )

        return await _parse_with_openai_compatible(
            text,
            config["api_key"],
            config["model"],
            config["base_url"],
        )
    except Exception as exc:
        logger.error("AI API error provider=%s: %s", config["provider"], exc)
        raise
