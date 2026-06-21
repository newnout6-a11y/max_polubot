import os
from dataclasses import dataclass
from typing import Iterable

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int_env(name: str, default: int) -> int:
    value = _env(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _float_env(name: str, default: float) -> float:
    value = _env(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc


def _bool_env(name: str, default: bool) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on", "y"}


def _csv_int_env(name: str) -> list[int]:
    raw = _env(name)
    if not raw:
        return []

    values: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError as exc:
            raise ValueError(f"{name} contains non-integer value {item!r}") from exc
    return values


def _csv_str_env(name: str, default: Iterable[str]) -> list[str]:
    raw = _env(name)
    if not raw:
        return [item for item in default if item]
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class StartupValidation:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


TARGET_CHAT_ID = _int_env("TARGET_CHAT_ID", 0)
ADMIN_IDS = _csv_int_env("ADMIN_IDS")
SESSION_FILE = _env("SESSION_FILE", "session.json")

GEMINI_API_KEY = _env("GEMINI_API_KEY")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-3.1-pro")
AI_PROVIDER = _env("AI_PROVIDER", "gemini").lower()
OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-5.5")
OPENAI_BASE_URL = _env("OPENAI_BASE_URL", "https://api.byesu.com")
OPENAI_WIRE_API = _env("OPENAI_WIRE_API", "responses")
OPENAI_REASONING_EFFORT = _env("OPENAI_REASONING_EFFORT", "medium")
DISABLE_RESPONSE_STORAGE = _bool_env("DISABLE_RESPONSE_STORAGE", False)
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = _env("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_WIRE_API = _env("DEEPSEEK_WIRE_API", "chat_completions")
DEEPSEEK_REASONING_EFFORT = _env("DEEPSEEK_REASONING_EFFORT", "")
AI_REQUEST_TIMEOUT_SECONDS = _float_env("AI_REQUEST_TIMEOUT_SECONDS", 45.0)
AI_LOOP_INTERVAL_SECONDS = _float_env("AI_LOOP_INTERVAL_SECONDS", 15.0)
AI_MESSAGE_DELAY_SECONDS = _float_env("AI_MESSAGE_DELAY_SECONDS", 3.0)
AI_BATCH_LIMIT = _int_env("AI_BATCH_LIMIT", 20)
AI_MAX_PARSE_ATTEMPTS = _int_env("AI_MAX_PARSE_ATTEMPTS", 5)
AI_RETRY_BASE_SECONDS = _float_env("AI_RETRY_BASE_SECONDS", 60.0)
AI_RETRY_MAX_SECONDS = _float_env("AI_RETRY_MAX_SECONDS", 3600.0)
AI_CONFIG_ERROR_COOLDOWN_SECONDS = _float_env("AI_CONFIG_ERROR_COOLDOWN_SECONDS", 900.0)
AI_BACKGROUND_PROCESSING = _bool_env("AI_BACKGROUND_PROCESSING", False)

DATABASE_URL = _env("DATABASE_URL")
NEON_DATABASE_URL = _env("NEON_DATABASE_URL")
DATABASE_URL = DATABASE_URL or NEON_DATABASE_URL
DB_POOL_MIN_SIZE = _int_env("DB_POOL_MIN_SIZE", 0)
DB_POOL_MAX_SIZE = _int_env("DB_POOL_MAX_SIZE", 5)
DB_CONNECT_TIMEOUT_SECONDS = _int_env("DB_CONNECT_TIMEOUT_SECONDS", 10)
DB_SSLMODE = _env("DB_SSLMODE", "require")

REPORT_DAY_OF_WEEK = _env("REPORT_DAY_OF_WEEK", "fri")
REPORT_HOUR = _int_env("REPORT_HOUR", 18)
REPORT_MINUTE = _int_env("REPORT_MINUTE", 0)

COMMAND_PREFIX = _env("COMMAND_PREFIX", "!")
COMMAND_ALIASES_PING = _csv_str_env("COMMAND_ALIASES_PING", ("\u043f\u0438\u043d\u0433", "ping"))
COMMAND_ALIASES_STATS = _csv_str_env("COMMAND_ALIASES_STATS", ("\u0441\u0442\u0430\u0442\u0430", "stats"))
COMMAND_ALIASES_PARSE_FINANCE = _csv_str_env(
    "COMMAND_ALIASES_PARSE_FINANCE",
    ("\u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c", "parse_finance", "parse"),
)
COMMAND_ALIASES_ASK_AI = _csv_str_env(
    "COMMAND_ALIASES_ASK_AI",
    ("ai", "\u0430\u0439", "\u0441\u043f\u0440\u043e\u0441\u0438"),
)
COMMAND_ALIASES_HISTORY = _csv_str_env(
    "COMMAND_ALIASES_HISTORY",
    ("\u0438\u0441\u0442\u043e\u0440\u0438\u044f", "history", "backfill"),
)
COMMAND_ALIASES_MESSAGES = _csv_str_env(
    "COMMAND_ALIASES_MESSAGES",
    ("\u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f", "messages", "saved"),
)
COMMAND_ALIASES_HELP = _csv_str_env("COMMAND_ALIASES_HELP", ("\u0445\u0435\u043b\u043f", "help"))
COMMAND_ALIASES_STATUS = _csv_str_env(
    "COMMAND_ALIASES_STATUS",
    ("\u0441\u0442\u0430\u0442\u0443\u0441", "status"),
)
COMMAND_ALIASES_CHECKS = _csv_str_env(
    "COMMAND_ALIASES_CHECKS",
    ("\u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430", "check"),
)
COMMAND_ALIASES_SETUP = _csv_str_env(
    "COMMAND_ALIASES_SETUP",
    ("\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430", "setup", "settings"),
)
COMMAND_ALIASES_CHAT = _csv_str_env(
    "COMMAND_ALIASES_CHAT",
    ("\u0447\u0430\u0442", "chat", "chat_id"),
)
COMMAND_ALIASES_CLEAR_AI = _csv_str_env(
    "COMMAND_ALIASES_CLEAR_AI",
    ("\u043e\u0447\u0438\u0441\u0442\u0438\u0442\u044c_ai", "clear_ai", "clear_pending"),
)
COMMAND_ALIASES_ME = _csv_str_env(
    "COMMAND_ALIASES_ME",
    ("\u043a\u0442\u043e\u044f", "me", "my_id"),
)

QUEUE_MIN_DELAY = _float_env("QUEUE_MIN_DELAY", 3.0)
QUEUE_MAX_DELAY = _float_env("QUEUE_MAX_DELAY", 7.0)
QUEUE_TYPING_CHARS_PER_SECOND = _float_env("QUEUE_TYPING_CHARS_PER_SECOND", 18.0)
QUEUE_TYPING_MAX_DELAY = _float_env("QUEUE_TYPING_MAX_DELAY", 8.0)
QUEUE_MAX_SIZE = _int_env("QUEUE_MAX_SIZE", 100)
QUEUE_SEND_RETRIES = _int_env("QUEUE_SEND_RETRIES", 5)
QUEUE_RETRY_DELAY_SECONDS = _float_env("QUEUE_RETRY_DELAY_SECONDS", 5.0)
QUEUE_PUT_TIMEOUT_SECONDS = _float_env("QUEUE_PUT_TIMEOUT_SECONDS", 2.0)

HISTORY_DEFAULT_DAYS = _int_env("HISTORY_DEFAULT_DAYS", 10)
HISTORY_MAX_DAYS = _int_env("HISTORY_MAX_DAYS", 30)
HISTORY_PAGE_SIZE = _int_env("HISTORY_PAGE_SIZE", 100)
HISTORY_MAX_MESSAGES = _int_env("HISTORY_MAX_MESSAGES", 2000)

MAX_WS_URL = _env("MAX_WS_URL", "wss://ws-api.oneme.ru/websocket")
MAX_WS_ORIGIN = _env("MAX_WS_ORIGIN", "https://web.max.ru")
MAX_USER_AGENT = _env(
    "MAX_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
)
MAX_APP_VERSION = _env("MAX_APP_VERSION", "26.2.2")
MAX_DEVICE_TYPE = _env("MAX_DEVICE_TYPE", "WEB")
MAX_DEVICE_NAME = _env("MAX_DEVICE_NAME", "Chrome")
MAX_OS_VERSION = _env("MAX_OS_VERSION", "Windows")
MAX_LOCALE = _env("MAX_LOCALE", "ru")
MAX_DEVICE_LOCALE = _env("MAX_DEVICE_LOCALE", MAX_LOCALE)
MAX_SCREEN = _env("MAX_SCREEN", "1920x1080 1.0x")
MAX_TIMEZONE = _env("MAX_TIMEZONE", "Europe/Moscow")
MAX_PROTOCOL_VERSION = _int_env("MAX_PROTOCOL_VERSION", 11)
MAX_REQUEST_TIMEOUT_SECONDS = _float_env("MAX_REQUEST_TIMEOUT_SECONDS", 20.0)
MAX_KEEPALIVE_INTERVAL_SECONDS = _float_env("MAX_KEEPALIVE_INTERVAL_SECONDS", 30.0)
MAX_BACKOFF_INITIAL_SECONDS = _float_env("MAX_BACKOFF_INITIAL_SECONDS", 1.0)
MAX_BACKOFF_MAX_SECONDS = _float_env("MAX_BACKOFF_MAX_SECONDS", 60.0)

SESSION_CHECK_TIMEOUT_SECONDS = _float_env("SESSION_CHECK_TIMEOUT_SECONDS", 20.0)
STARTUP_SESSION_CHECK = _bool_env("STARTUP_SESSION_CHECK", True)
WATCHDOG_INTERVAL_SECONDS = _float_env("WATCHDOG_INTERVAL_SECONDS", 60.0)
WATCHDOG_SESSION_CHECK_INTERVAL_SECONDS = _float_env(
    "WATCHDOG_SESSION_CHECK_INTERVAL_SECONDS",
    300.0,
)
READINESS_MAX_DISCONNECTED_SECONDS = _float_env("READINESS_MAX_DISCONNECTED_SECONDS", 180.0)

WEB_HOST = _env("WEB_HOST", "0.0.0.0")
WEB_PORT = _int_env("WEB_PORT", _int_env("PORT", 7860))


def validate_startup_config() -> StartupValidation:
    errors: list[str] = []
    warnings: list[str] = []

    if not DATABASE_URL:
        errors.append("DATABASE_URL or NEON_DATABASE_URL is required")
    if not TARGET_CHAT_ID:
        warnings.append("TARGET_CHAT_ID is empty: use chat command to set target_chat_id")
    if not ADMIN_IDS:
        warnings.append("ADMIN_IDS is empty: only bootstrap id commands will be accepted")
    if AI_PROVIDER == "gemini" and not GEMINI_API_KEY:
        warnings.append("GEMINI_API_KEY is empty: AI parsing will be paused")
    if AI_PROVIDER == "openai" and not OPENAI_API_KEY:
        warnings.append("OPENAI_API_KEY is empty: AI parsing will be paused")
    if AI_PROVIDER == "deepseek" and not DEEPSEEK_API_KEY:
        warnings.append("DEEPSEEK_API_KEY is empty: AI parsing will be paused")
    if AI_PROVIDER not in {"gemini", "openai", "deepseek"}:
        errors.append("AI_PROVIDER must be one of: gemini, openai, deepseek")
    if QUEUE_MIN_DELAY > QUEUE_MAX_DELAY:
        errors.append("QUEUE_MIN_DELAY must be less than or equal to QUEUE_MAX_DELAY")
    if QUEUE_TYPING_CHARS_PER_SECOND <= 0:
        errors.append("QUEUE_TYPING_CHARS_PER_SECOND must be positive")
    if QUEUE_TYPING_MAX_DELAY < 0:
        errors.append("QUEUE_TYPING_MAX_DELAY cannot be negative")
    if DB_POOL_MIN_SIZE > DB_POOL_MAX_SIZE:
        errors.append("DB_POOL_MIN_SIZE must be less than or equal to DB_POOL_MAX_SIZE")
    if not COMMAND_PREFIX:
        errors.append("COMMAND_PREFIX cannot be empty")
    if AI_BATCH_LIMIT <= 0:
        errors.append("AI_BATCH_LIMIT must be positive")
    if AI_MAX_PARSE_ATTEMPTS <= 0:
        errors.append("AI_MAX_PARSE_ATTEMPTS must be positive")
    if AI_RETRY_BASE_SECONDS <= 0:
        errors.append("AI_RETRY_BASE_SECONDS must be positive")
    if AI_RETRY_MAX_SECONDS < AI_RETRY_BASE_SECONDS:
        errors.append("AI_RETRY_MAX_SECONDS must be greater than or equal to AI_RETRY_BASE_SECONDS")
    if AI_CONFIG_ERROR_COOLDOWN_SECONDS <= 0:
        errors.append("AI_CONFIG_ERROR_COOLDOWN_SECONDS must be positive")
    if QUEUE_MAX_SIZE <= 0:
        errors.append("QUEUE_MAX_SIZE must be positive")
    if QUEUE_SEND_RETRIES < 0:
        errors.append("QUEUE_SEND_RETRIES cannot be negative")
    if HISTORY_DEFAULT_DAYS <= 0:
        errors.append("HISTORY_DEFAULT_DAYS must be positive")
    if HISTORY_MAX_DAYS < HISTORY_DEFAULT_DAYS:
        errors.append("HISTORY_MAX_DAYS must be greater than or equal to HISTORY_DEFAULT_DAYS")
    if HISTORY_PAGE_SIZE <= 0:
        errors.append("HISTORY_PAGE_SIZE must be positive")
    if HISTORY_MAX_MESSAGES <= 0:
        errors.append("HISTORY_MAX_MESSAGES must be positive")
    if not 0 <= REPORT_HOUR <= 23:
        errors.append("REPORT_HOUR must be between 0 and 23")
    if not 0 <= REPORT_MINUTE <= 59:
        errors.append("REPORT_MINUTE must be between 0 and 59")
    if MAX_REQUEST_TIMEOUT_SECONDS <= 0:
        errors.append("MAX_REQUEST_TIMEOUT_SECONDS must be positive")
    if MAX_KEEPALIVE_INTERVAL_SECONDS <= 0:
        errors.append("MAX_KEEPALIVE_INTERVAL_SECONDS must be positive")
    if WATCHDOG_INTERVAL_SECONDS <= 0:
        errors.append("WATCHDOG_INTERVAL_SECONDS must be positive")
    if SESSION_CHECK_TIMEOUT_SECONDS <= 0:
        errors.append("SESSION_CHECK_TIMEOUT_SECONDS must be positive")
    if not MAX_DEVICE_TYPE:
        errors.append("MAX_DEVICE_TYPE cannot be empty")

    return StartupValidation(errors=errors, warnings=warnings)
