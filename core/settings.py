import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SettingSpec:
    key: str
    value_type: type
    default: Any
    secret: bool = False
    editable: bool = True
    description: str = ""

    def parse(self, raw: str):
        value = raw.strip()
        if self.value_type is bool:
            return value.lower() in {"1", "true", "yes", "on", "y", "\u0434\u0430"}
        if self.value_type is int:
            return int(value)
        if self.value_type is float:
            return float(value)
        return value


SETTINGS = {
    "ai_provider": SettingSpec(
        key="ai_provider",
        value_type=str,
        default="gemini",
        description="AI provider: gemini, openai, deepseek.",
    ),
    "target_chat_id": SettingSpec(
        key="target_chat_id",
        value_type=int,
        default=0,
        description="Chat that bot reads (read-only). Use 'here' in setup command.",
    ),
    "report_chat_id": SettingSpec(
        key="report_chat_id",
        value_type=int,
        default=0,
        description="Chat where bot sends replies and reports (e.g. Saved Messages). Use 'here' in setup command.",
    ),
    "gemini_api_key": SettingSpec(
        key="gemini_api_key",
        value_type=str,
        default="",
        secret=True,
        description="Google Gemini API key used for financial parsing.",
    ),
    "gemini_model": SettingSpec(
        key="gemini_model",
        value_type=str,
        default="gemini-3.1-pro",
        description="Gemini model name.",
    ),
    "openai_api_key": SettingSpec(
        key="openai_api_key",
        value_type=str,
        default="",
        secret=True,
        description="OpenAI API key.",
    ),
    "openai_model": SettingSpec(
        key="openai_model",
        value_type=str,
        default="gpt-5.5",
        description="OpenAI chat model.",
    ),
    "openai_base_url": SettingSpec(
        key="openai_base_url",
        value_type=str,
        default="https://api.byesu.com",
        description="OpenAI-compatible base URL.",
    ),
    "openai_wire_api": SettingSpec(
        key="openai_wire_api",
        value_type=str,
        default="responses",
        description="OpenAI wire API: responses or chat_completions.",
    ),
    "openai_reasoning_effort": SettingSpec(
        key="openai_reasoning_effort",
        value_type=str,
        default="medium",
        description="Reasoning effort for Responses API.",
    ),
    "disable_response_storage": SettingSpec(
        key="disable_response_storage",
        value_type=bool,
        default=False,
        description="When true, sends store=false to Responses API.",
    ),
    "ai_parse_batch_size": SettingSpec(
        key="ai_parse_batch_size",
        value_type=int,
        default=50,
        description="Saved chat messages sent to AI in one finance parsing request.",
    ),
    "deepseek_api_key": SettingSpec(
        key="deepseek_api_key",
        value_type=str,
        default="",
        secret=True,
        description="DeepSeek API key.",
    ),
    "deepseek_model": SettingSpec(
        key="deepseek_model",
        value_type=str,
        default="deepseek-v4-pro",
        description="DeepSeek chat model.",
    ),
    "deepseek_base_url": SettingSpec(
        key="deepseek_base_url",
        value_type=str,
        default="https://api.deepseek.com/v1",
        description="DeepSeek OpenAI-compatible base URL.",
    ),
    "deepseek_wire_api": SettingSpec(
        key="deepseek_wire_api",
        value_type=str,
        default="chat_completions",
        description="DeepSeek wire API: responses or chat_completions.",
    ),
    "deepseek_reasoning_effort": SettingSpec(
        key="deepseek_reasoning_effort",
        value_type=str,
        default="",
        description="Optional reasoning effort for compatible Responses API.",
    ),
    "report_day_of_week": SettingSpec(
        key="report_day_of_week",
        value_type=str,
        default="fri",
        description="Weekly report day for APScheduler cron trigger.",
    ),
    "report_hour": SettingSpec(
        key="report_hour",
        value_type=int,
        default=18,
        description="Weekly report hour, 0-23.",
    ),
    "report_minute": SettingSpec(
        key="report_minute",
        value_type=int,
        default=0,
        description="Weekly report minute, 0-59.",
    ),
    "queue_min_delay": SettingSpec(
        key="queue_min_delay",
        value_type=float,
        default=3.0,
        description="Minimum delay before sending queued messages.",
    ),
    "queue_max_delay": SettingSpec(
        key="queue_max_delay",
        value_type=float,
        default=7.0,
        description="Maximum delay before sending queued messages.",
    ),
    "queue_typing_chars_per_second": SettingSpec(
        key="queue_typing_chars_per_second",
        value_type=float,
        default=18.0,
        description="Controls synthetic typing delay. Higher means faster replies.",
    ),
    "queue_typing_max_delay": SettingSpec(
        key="queue_typing_max_delay",
        value_type=float,
        default=8.0,
        description="Hard cap for synthetic typing delay.",
    ),
}


class RuntimeSettings:
    def __init__(self, initial: dict[str, Any] | None = None):
        self._values = {key: spec.default for key, spec in SETTINGS.items()}
        if initial:
            self._values.update(initial)

    def get(self, key: str):
        return self._values[normalize_key(key)]

    def set(self, key: str, value):
        normalized = normalize_key(key)
        self._values[normalized] = value

    def as_dict(self, masked: bool = False):
        result = {}
        for key, value in self._values.items():
            spec = SETTINGS[key]
            result[key] = mask_value(value) if masked and spec.secret else value
        return result


def normalize_key(key: str) -> str:
    normalized = key.strip().lower().replace("-", "_")
    aliases = {
        "chat": "target_chat_id",
        "target": "target_chat_id",
        "target_chat": "target_chat_id",
        "report_chat": "report_chat_id",
        "report": "report_chat_id",
        "reply_chat": "report_chat_id",
        "gemini_key": "gemini_api_key",
        "api_key": "gemini_api_key",
        "model": "gemini_model",
        "provider": "ai_provider",
        "openai_key": "openai_api_key",
        "chatgpt_key": "openai_api_key",
        "chatgpt_model": "openai_model",
        "wire_api": "openai_wire_api",
        "reasoning_effort": "openai_reasoning_effort",
        "disable_storage": "disable_response_storage",
        "parse_batch": "ai_parse_batch_size",
        "batch_size": "ai_parse_batch_size",
        "deepseek_key": "deepseek_api_key",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in SETTINGS:
        raise KeyError(f"Unknown setting: {key}")
    return normalized


def parse_setting_value(key: str, raw: str, context: dict | None = None):
    normalized = normalize_key(key)
    spec = SETTINGS[normalized]
    value = raw.strip()

    if normalized == "target_chat_id" and value.lower() in {
        "here",
        "this",
        "\u0442\u0443\u0442",
        "\u0441\u044e\u0434\u0430",
    }:
        chat_id = (context or {}).get("chat_id")
        if not chat_id:
            raise ValueError("Current chat id is not available for 'here'.")
        return int(chat_id)

    parsed = spec.parse(value)
    validate_setting(normalized, parsed)
    return parsed


def validate_setting(key: str, value):
    if key == "ai_provider" and str(value).lower() not in {"gemini", "openai", "deepseek"}:
        raise ValueError("ai_provider must be one of: gemini, openai, deepseek")
    if key in {"openai_wire_api", "deepseek_wire_api"} and str(value).lower() not in {
        "responses",
        "chat_completions",
    }:
        raise ValueError(f"{key} must be responses or chat_completions")
    if key == "target_chat_id" and not int(value):
        raise ValueError("target_chat_id must be non-zero")
    if key == "report_hour" and not 0 <= int(value) <= 23:
        raise ValueError("report_hour must be between 0 and 23")
    if key == "report_minute" and not 0 <= int(value) <= 59:
        raise ValueError("report_minute must be between 0 and 59")
    if key == "queue_min_delay" and float(value) < 0:
        raise ValueError("queue_min_delay cannot be negative")
    if key == "queue_max_delay" and float(value) < 0:
        raise ValueError("queue_max_delay cannot be negative")
    if key == "queue_typing_chars_per_second" and float(value) <= 0:
        raise ValueError("queue_typing_chars_per_second must be positive")
    if key == "queue_typing_max_delay" and float(value) < 0:
        raise ValueError("queue_typing_max_delay cannot be negative")
    if key == "ai_parse_batch_size" and int(value) <= 0:
        raise ValueError("ai_parse_batch_size must be positive")


def mask_value(value) -> str:
    text = str(value or "")
    if not text:
        return "<empty>"
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"


def format_settings(settings: RuntimeSettings) -> str:
    lines = ["\u0422\u0435\u043a\u0443\u0449\u0438\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438:"]
    values = settings.as_dict(masked=True)
    for key in sorted(values):
        lines.append(f"- {key}: {values[key]}")
    return "\n".join(lines)
