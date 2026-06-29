import asyncio
import json
import logging
import re
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


class AIProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, response_body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

    @property
    def is_config_error(self) -> bool:
        return self.status_code is not None and 400 <= self.status_code < 500


def _response_json(response: httpx.Response, provider: str):
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        body = _short_body(response.text)
        raise AIProviderError(
            f"{provider} API returned non-JSON body: {body or 'empty response'}",
            status_code=response.status_code,
            response_body=body,
        ) from exc


class Transaction(BaseModel):
    category: str = Field(description="Lowercase category or item name.")
    expense: int = Field(default=0, ge=0, description="Amount spent in rubles, positive integer.")
    income: int = Field(default=0, ge=0, description="Amount earned in rubles, positive integer.")
    explanation: str = Field(default="", description="Brief explanation of why this is a financial operation, citing the exact words from the message.")

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("category cannot be empty")
        return normalized


class ExtractionResult(BaseModel):
    transactions: List[Transaction] = Field(default_factory=list)


class MessageExtraction(BaseModel):
    message_id: str
    transactions: List[Transaction] = Field(default_factory=list)


class BatchExtractionResult(BaseModel):
    messages: List[MessageExtraction] = Field(default_factory=list)


FINANCE_INSTRUCTIONS = "Return only valid JSON."
FINANCE_BATCH_INSTRUCTIONS = (
    "Return only valid JSON. Extract finance transactions from chat messages. "
    "Include every input message_id exactly once."
)
GENERAL_AI_INSTRUCTIONS = (
    "You are a helpful assistant inside a MAX chat bot. "
    "Answer concisely, in Russian by default, unless the user asks otherwise."
)
GENERAL_AI_JSON_INSTRUCTIONS = (
    "Return only valid JSON with this schema: "
    '{"answer":"short helpful answer as a string"}. '
    "Answer in Russian by default, unless the user asks otherwise."
)


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


def _short_body(text: str, limit: int = 800) -> str:
    compact = " ".join((text or "").split())
    if len(compact) > limit:
        return compact[:limit] + "..."
    return compact


def _raise_ai_status(response: httpx.Response, provider: str):
    if response.status_code < 400:
        return
    body = _short_body(response.text)
    raise AIProviderError(
        f"{provider} API HTTP {response.status_code}: {body or response.reason_phrase}",
        status_code=response.status_code,
        response_body=body,
    )


def _prompt(text: str, sender_name: str | None = None) -> str:
    sender_line = f"От: {sender_name}\n" if sender_name else ""
    return (
        "Верни только JSON. Проанализируй сообщение чата и извлеки финансовые операции. "
        "Схема ответа: "
        '{"transactions":[{"category":"string","expense":0,"income":0,"explanation":"string"}]}. '
        'Если в сообщении нет финансовых операций, верни {"transactions":[]}. '
        "Финансовая операция — это любое упоминание денег: покупка, оплата, перевод, "
        "получение, списание, долг, возврат, зарплата, аренда, коммуналка и т.д. "
        "Не используй жёсткий список ключевых слов — анализируй контекст. "
        "Например, 'отдал 500 за обед' — это расход 500, 'принесли 3000 аванс' — доход 3000. "
        "Если сообщение не содержит чисел или упоминания денег — это не финансовая операция. "
        "В поле explanation укажи цитату из сообщения, которая подтверждает операцию. "
        "Не придумывай суммы. Сообщение: "
        f"{sender_line}{json.dumps(text, ensure_ascii=False)}"
    )


def _batch_prompt(messages: list[dict]) -> str:
    payload = [
        {
            "message_id": str(message["id"]),
            "sender": str(message.get("sender_name") or ""),
            "text": str(message.get("text") or ""),
        }
        for message in messages
    ]
    return (
        "Верни только JSON. Проанализируй список сообщений чата и извлеки финансовые операции. "
        "Схема ответа: "
        '{"messages":[{"message_id":"string","transactions":[{"category":"string","expense":0,"income":0,"explanation":"string"}]}]}. '
        "Для каждого входного message_id верни ровно один объект в messages. "
        "Если в сообщении нет финансовых операций, верни \"transactions\":[] для этого message_id. "
        "Финансовая операция — это любое упоминание денег: покупка, оплата, перевод, "
        "получение, списание, долг, возврат, зарплата, аренда, коммуналка и т.д. "
        "Не используй жёсткий список ключевых слов — анализируй контекст. "
        "Если сообщение не содержит чисел или упоминания денег — это не финансовая операция. "
        "В поле explanation укажи цитату из сообщения, которая подтверждает операцию. "
        "Не придумывай суммы и не смешивай разные message_id. "
        "Сообщения JSON: "
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _plain_prompt(text: str) -> str:
    return text.strip()


def _answer_prompt(text: str) -> str:
    return (
        "Return only valid JSON. "
        "Schema: {\"answer\":\"string\"}. "
        "User question: "
        f"{json.dumps(text, ensure_ascii=False)}"
    )


def _is_byesu_base_url(base_url: str) -> bool:
    return "byesu.com" in str(base_url).lower()


async def _parse_with_gemini(text: str, api_key: str, model: str, sender_name: str | None = None) -> List[Transaction]:
    client = _get_gemini_client(api_key)
    if not client:
        raise RuntimeError("Gemini API client is not initialized.")

    loop = asyncio.get_running_loop()

    def run_sync():
        return client.models.generate_content(
            model=model,
            contents=_prompt(text, sender_name=sender_name),
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


async def _parse_batch_with_gemini(messages: list[dict], api_key: str, model: str) -> dict[str, List[Transaction]]:
    client = _get_gemini_client(api_key)
    if not client:
        raise RuntimeError("Gemini API client is not initialized.")

    loop = asyncio.get_running_loop()

    def run_sync():
        return client.models.generate_content(
            model=model,
            contents=_batch_prompt(messages),
            config={
                "response_mime_type": "application/json",
                "response_schema": BatchExtractionResult,
            },
        )

    response = await asyncio.wait_for(
        loop.run_in_executor(None, run_sync),
        timeout=AI_REQUEST_TIMEOUT_SECONDS,
    )

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, BatchExtractionResult):
        result = parsed
    elif isinstance(parsed, dict):
        result = BatchExtractionResult(**parsed)
    else:
        result = BatchExtractionResult(**json.loads(response.text or "{}"))

    return {item.message_id: item.transactions for item in result.messages}


async def _chat_completions_request(
    prompt: str,
    api_key: str,
    model: str,
    base_url: str,
    instructions: str,
    json_mode: bool = False,
):
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
                "content": instructions,
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(url, headers=headers, json=payload)
        _raise_ai_status(response, "chat_completions")
        data = _response_json(response, "chat_completions")

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIProviderError(
            "chat_completions API returned an unexpected response shape",
            status_code=response.status_code,
            response_body=_short_body(response.text),
        ) from exc


async def _parse_with_openai_compatible(text: str, api_key: str, model: str, base_url: str, sender_name: str | None = None):
    content = await _chat_completions_request(
        _prompt(text, sender_name=sender_name),
        api_key,
        model,
        base_url,
        FINANCE_INSTRUCTIONS,
        json_mode=True,
    )
    return ExtractionResult(**_loads_json_object(content)).transactions


async def _parse_batch_with_openai_compatible(
    messages: list[dict],
    api_key: str,
    model: str,
    base_url: str,
):
    content = await _chat_completions_request(
        _batch_prompt(messages),
        api_key,
        model,
        base_url,
        FINANCE_BATCH_INSTRUCTIONS,
        json_mode=True,
    )
    result = BatchExtractionResult(**_loads_json_object(content))
    return {item.message_id: item.transactions for item in result.messages}


def _extract_response_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"]
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                return content["text"]
    raise RuntimeError("Responses API returned no output text")


async def _extract_response_text_from_sse(response: httpx.Response, provider: str) -> str:
    parts: list[str] = []
    last_error = ""

    async for line in response.aiter_lines():
        if not line or not line.startswith("data:"):
            continue

        payload = line[5:].strip()
        if payload == "[DONE]":
            continue

        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            last_error = _short_body(payload)
            continue

        event_type = event.get("type")
        if event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
            parts.append(event["delta"])
            continue

        if event_type in {"response.failed", "response.incomplete"}:
            error = event.get("error") or event.get("response", {}).get("error") or event
            last_error = _short_body(json.dumps(error, ensure_ascii=False))

        if event_type == "error":
            last_error = _short_body(json.dumps(event, ensure_ascii=False))

    text = "".join(parts).strip()
    if text:
        return text

    raise AIProviderError(
        f"{provider} streaming API returned no output text: {last_error or 'empty stream'}",
        status_code=response.status_code,
        response_body=last_error,
    )


def _loads_json_object(text: str) -> dict:
    value = (text or "").strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", value)
        if not match:
            raise
        return json.loads(match.group(0))


async def _parse_with_responses_api(
    prompt: str,
    api_key: str,
    model: str,
    base_url: str,
    reasoning_effort: str,
    disable_response_storage: bool,
    instructions: str,
    json_mode: bool = False,
):
    url = base_url.rstrip("/") + "/responses"
    is_byesu = _is_byesu_base_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if is_byesu:
        payload = {
            "model": model,
            "instructions": instructions,
            "input": [
                {"role": "user", "content": prompt},
            ],
            "stream": True,
        }
    else:
        payload = {
            "model": model,
            "instructions": instructions,
            "input": [
                {"role": "user", "content": prompt},
            ],
        }
    if json_mode and not is_byesu:
        payload["text"] = {"format": {"type": "json_object"}}

    if reasoning_effort and not is_byesu:
        payload["reasoning"] = {"effort": reasoning_effort}
    if disable_response_storage and not is_byesu:
        payload["store"] = False

    if is_byesu:
        logger.info(
            "Byesu responses payload: keys=%s input_type=%s json_mode=%s has_reasoning=%s has_store=%s",
            sorted(payload.keys()),
            type(payload.get("input")).__name__,
            json_mode,
            "reasoning" in payload,
            "store" in payload,
        )

    async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT_SECONDS) as client:
        if is_byesu:
            headers["Accept"] = "text/event-stream"
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    await response.aread()
                    _raise_ai_status(response, "responses")
                return await _extract_response_text_from_sse(response, "responses")

        response = await client.post(url, headers=headers, json=payload)
        _raise_ai_status(response, "responses")
        data = _response_json(response, "responses")

    return _extract_response_text(data)


async def parse_financial_message(text: str, settings=None, sender_name: str | None = None) -> List[Transaction]:
    """Parse a chat message into financial transactions."""
    if not text.strip():
        return []

    config = _provider_config(settings)
    if not config["api_key"]:
        raise RuntimeError(f"{config['provider']} API key is not configured.")

    try:
        if config["provider"] == "gemini":
            return await _parse_with_gemini(text, config["api_key"], config["model"], sender_name=sender_name)

        if config["wire_api"] == "responses":
            try:
                content = await _parse_with_responses_api(
                    _prompt(text, sender_name=sender_name),
                    config["api_key"],
                    config["model"],
                    config["base_url"],
                    config["reasoning_effort"],
                    config["disable_response_storage"],
                    FINANCE_INSTRUCTIONS,
                    json_mode=True,
                )
                return ExtractionResult(**_loads_json_object(content)).transactions
            except AIProviderError as exc:
                if (
                    (exc.status_code == 400 or (exc.status_code and exc.status_code >= 500))
                    and not _is_byesu_base_url(config["base_url"])
                ):
                    logger.warning(
                        "Responses API failed, falling back to chat_completions once: %s",
                        exc,
                    )
                    return await _parse_with_openai_compatible(
                        text,
                        config["api_key"],
                        config["model"],
                        config["base_url"],
                        sender_name=sender_name,
                    )
                raise

        return await _parse_with_openai_compatible(
            text,
            config["api_key"],
            config["model"],
            config["base_url"],
            sender_name=sender_name,
        )
    except Exception as exc:
        logger.error("AI API error provider=%s: %s", config["provider"], exc)
        raise


async def parse_financial_messages_batch(messages: list[dict], settings=None) -> dict[str, List[Transaction]]:
    """Parse saved chat messages into financial transactions keyed by message id."""
    normalized_messages = [
        {
            "id": str(message["id"]),
            "text": str(message.get("text") or "").strip(),
            "sender_name": str(message.get("sender_name") or "").strip(),
        }
        for message in messages
        if str(message.get("text") or "").strip()
    ]
    if not normalized_messages:
        return {}

    config = _provider_config(settings)
    if not config["api_key"]:
        raise RuntimeError(f"{config['provider']} API key is not configured.")

    try:
        if config["provider"] == "gemini":
            return await _parse_batch_with_gemini(
                normalized_messages,
                config["api_key"],
                config["model"],
            )

        if config["wire_api"] == "responses":
            try:
                content = await _parse_with_responses_api(
                    _batch_prompt(normalized_messages),
                    config["api_key"],
                    config["model"],
                    config["base_url"],
                    config["reasoning_effort"],
                    config["disable_response_storage"],
                    FINANCE_BATCH_INSTRUCTIONS,
                    json_mode=True,
                )
                result = BatchExtractionResult(**_loads_json_object(content))
                return {item.message_id: item.transactions for item in result.messages}
            except AIProviderError as exc:
                if (
                    (exc.status_code == 400 or (exc.status_code and exc.status_code >= 500))
                    and not _is_byesu_base_url(config["base_url"])
                ):
                    logger.warning(
                        "Responses API failed for batch, falling back to chat_completions once: %s",
                        exc,
                    )
                    return await _parse_batch_with_openai_compatible(
                        normalized_messages,
                        config["api_key"],
                        config["model"],
                        config["base_url"],
                    )
                raise

        return await _parse_batch_with_openai_compatible(
            normalized_messages,
            config["api_key"],
            config["model"],
            config["base_url"],
        )
    except Exception as exc:
        logger.error("AI batch API error provider=%s: %s", config["provider"], exc)
        raise


async def ask_ai(question: str, settings=None) -> str:
    config = _provider_config(settings)
    if not config["api_key"]:
        raise RuntimeError(f"{config['provider']} API key is not configured.")

    prompt = _plain_prompt(question)
    if not prompt:
        raise ValueError("Question cannot be empty")

    if config["provider"] == "gemini":
        client = _get_gemini_client(config["api_key"])
        if not client:
            raise RuntimeError("Gemini API client is not initialized.")
        loop = asyncio.get_running_loop()

        def run_sync():
            return client.models.generate_content(
                model=config["model"],
                contents=f"{GENERAL_AI_INSTRUCTIONS}\n\nUser question:\n{prompt}",
            )

        response = await asyncio.wait_for(
            loop.run_in_executor(None, run_sync),
            timeout=AI_REQUEST_TIMEOUT_SECONDS,
        )
        return (response.text or "").strip()

    if config["wire_api"] == "responses":
        try:
            content = await _parse_with_responses_api(
                prompt,
                config["api_key"],
                config["model"],
                config["base_url"],
                config["reasoning_effort"],
                config["disable_response_storage"],
                GENERAL_AI_INSTRUCTIONS,
                json_mode=False,
            )
            answer = content.strip()
            if not answer:
                raise AIProviderError("responses API returned empty answer")
            return answer
        except AIProviderError as exc:
            if exc.status_code and exc.status_code >= 500 and not _is_byesu_base_url(config["base_url"]):
                logger.warning(
                    "Responses API failed for general AI, falling back to chat_completions: %s",
                    exc,
                )
                return await _chat_completions_request(
                    prompt,
                    config["api_key"],
                    config["model"],
                    config["base_url"],
                    GENERAL_AI_INSTRUCTIONS,
                    json_mode=False,
                )
            raise

    return await _chat_completions_request(
        prompt,
        config["api_key"],
        config["model"],
        config["base_url"],
        GENERAL_AI_INSTRUCTIONS,
        json_mode=False,
    )
