import asyncio
import json
import logging
import os
import time

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ai.parser import is_ai_available, parse_financial_message
from ai.parser import AIProviderError
from core.client import MaxWebsocketClient, SessionAuthError
from core.config import (
    AI_BACKGROUND_PROCESSING,
    AI_CONFIG_ERROR_COOLDOWN_SECONDS,
    ADMIN_IDS,
    AI_BATCH_LIMIT,
    AI_LOOP_INTERVAL_SECONDS,
    AI_MESSAGE_DELAY_SECONDS,
    COMMAND_ALIASES_CHECKS,
    COMMAND_ALIASES_CHAT,
    COMMAND_ALIASES_CLEAR_AI,
    COMMAND_ALIASES_ASK_AI,
    COMMAND_ALIASES_HELP,
    COMMAND_ALIASES_HISTORY,
    COMMAND_ALIASES_ME,
    COMMAND_ALIASES_PARSE_FINANCE,
    COMMAND_ALIASES_PING,
    COMMAND_ALIASES_SETUP,
    COMMAND_ALIASES_STATS,
    COMMAND_ALIASES_STATUS,
    AI_PROVIDER,
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
    QUEUE_MAX_DELAY,
    QUEUE_MAX_SIZE,
    QUEUE_MIN_DELAY,
    QUEUE_TYPING_CHARS_PER_SECOND,
    QUEUE_TYPING_MAX_DELAY,
    READINESS_MAX_DISCONNECTED_SECONDS,
    REPORT_DAY_OF_WEEK,
    REPORT_HOUR,
    REPORT_MINUTE,
    SESSION_FILE,
    STARTUP_SESSION_CHECK,
    TARGET_CHAT_ID,
    WATCHDOG_INTERVAL_SECONDS,
    WATCHDOG_SESSION_CHECK_INTERVAL_SECONDS,
    WEB_HOST,
    WEB_PORT,
    validate_startup_config,
)
from core.dispatcher import Dispatcher
from core.queue import MessageQueue
from core.session_probe import probe_session
from core.settings import RuntimeSettings
from db.models import Database
from handlers.commands import (
    cmd_chat,
    cmd_checks,
    cmd_clear_pending,
    cmd_ask_ai,
    cmd_history,
    cmd_help,
    cmd_me,
    cmd_ping,
    cmd_parse_finance,
    cmd_setup,
    cmd_stata,
    cmd_status,
)
from handlers.finance import handle_financial_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="MAX Polubot")
runtime = {
    "started_at": int(time.time()),
    "client": None,
    "queue": None,
    "session_source": None,
    "settings": None,
    "last_checks": {},
    "ai_paused_until": 0,
    "shutting_down": False,
}


def _now() -> int:
    return int(time.time())


@app.get("/")
async def liveness():
    return {"status": "alive", "uptime_seconds": _now() - runtime["started_at"]}


@app.get("/health")
async def health_check():
    return await collect_status(include_db_stats=True)


@app.get("/ready")
async def readiness_check():
    status = await collect_status(include_db_stats=False)
    return JSONResponse(status_code=200 if status["ready"] else 503, content=status)


@app.get("/metrics")
async def metrics_check():
    return await collect_status(include_db_stats=True)


async def collect_status(include_db_stats: bool = False):
    client = runtime.get("client")
    queue = runtime.get("queue")
    settings = runtime.get("settings")
    client_status = client.status_snapshot() if client else {}
    queue_stats = queue.stats() if queue else {}

    db_ok = False
    db_stats = None
    db_error = None
    if Database.pool:
        try:
            db_ok = await Database.ping()
            if include_db_stats:
                db_stats = await Database.get_operational_stats()
        except Exception as exc:
            db_error = str(exc)

    disconnected_for = None
    if client and not client.authenticated:
        last_seen = client.last_authenticated_at or client.last_connected_at
        disconnected_for = _now() - last_seen if last_seen else None

    queue_ok = bool(queue_stats.get("worker_running", False))
    max_ok = bool(client_status.get("authenticated", False))
    ready = (
        not runtime["shutting_down"]
        and db_ok
        and queue_ok
        and max_ok
        and (
            disconnected_for is None
            or disconnected_for <= READINESS_MAX_DISCONNECTED_SECONDS
        )
    )

    checks = runtime.get("last_checks", {})
    return {
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "uptime_seconds": _now() - runtime["started_at"],
        "session_source": runtime.get("session_source"),
        "database": {"ok": db_ok, "error": db_error, "stats": db_stats},
        "max": client_status,
        "queue": queue_stats,
        "ai": {
            "available": is_ai_available(settings),
            "provider": settings.get("ai_provider") if settings else None,
        },
        "checks": checks,
    }


def load_session_credentials() -> tuple[str | None, str | None, str | None]:
    session_env = os.getenv("SESSION_JSON")
    if session_env:
        try:
            data = json.loads(session_env)
            device_id = data.get("deviceId") or data.get("device_id")
            token = data.get("token")
            if device_id and token:
                logger.info("Credentials loaded from SESSION_JSON.")
                return "SESSION_JSON", device_id, token
            logger.error("SESSION_JSON does not contain deviceId/device_id and token.")
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse SESSION_JSON: %s", exc)

    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            device_id = data.get("deviceId") or data.get("device_id")
            token = data.get("token")
            if device_id and token:
                logger.info("Credentials loaded from %s.", SESSION_FILE)
                return SESSION_FILE, device_id, token
            logger.error("%s does not contain deviceId/device_id and token.", SESSION_FILE)
    except FileNotFoundError:
        logger.error("Session file %s was not found.", SESSION_FILE)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse %s: %s", SESSION_FILE, exc)

    return None, None, None


async def run_startup_session_check(device_id, token) -> bool:
    if not STARTUP_SESSION_CHECK:
        logger.warning("Startup MAX session probe is disabled.")
        return True

    result = await probe_session(device_id, token)
    runtime["last_checks"]["session"] = {
        "ok": result.ok,
        "error": result.error,
        "message": result.message,
        "checked_at": _now(),
    }

    if result.ok:
        logger.info("Startup MAX session probe passed.")
        return True

    if result.invalid_session:
        logger.critical(
            "Startup MAX session probe failed: %s %s. Refresh SESSION_JSON with auth.py.",
            result.error,
            result.message or "",
        )
        return False

    logger.warning(
        "Startup MAX session probe could not verify the session: %s %s. Continuing.",
        result.error,
        result.message or "",
    )
    return True


async def background_ai_processor():
    """Parse stored messages in small batches without blocking the WebSocket listener."""
    if not AI_BACKGROUND_PROCESSING:
        logger.info("Background AI parsing is disabled; use parse command to process saved messages.")
        return

    while True:
        try:
            settings = runtime.get("settings")
            if not is_ai_available(settings):
                runtime["last_checks"]["ai"] = {
                    "ok": False,
                    "message": "AI API key is missing for selected provider",
                    "checked_at": _now(),
                }
                await asyncio.sleep(AI_LOOP_INTERVAL_SECONDS)
                continue

            if runtime.get("ai_paused_until", 0) > _now():
                await asyncio.sleep(AI_LOOP_INTERVAL_SECONDS)
                continue

            runtime["last_checks"]["ai"] = {"ok": True, "checked_at": _now()}
            unparsed = await Database.get_unparsed_messages(limit=AI_BATCH_LIMIT)
            for row in unparsed:
                msg_id = row["id"]
                text = row["text"]
                ts = row["timestamp"]

                logger.info("AI processing message %s...", msg_id)
                try:
                    transactions = await parse_financial_message(text, settings=settings)
                    await Database.replace_finances(msg_id, transactions, ts)
                    logger.info("Parsed %s transactions for message %s", len(transactions), msg_id)
                except AIProviderError as exc:
                    cooldown = AI_CONFIG_ERROR_COOLDOWN_SECONDS if exc.is_config_error else None
                    await Database.mark_parse_failed(msg_id, exc, retry_after_seconds=cooldown)
                    if exc.is_config_error:
                        runtime["ai_paused_until"] = _now() + int(AI_CONFIG_ERROR_COOLDOWN_SECONDS)
                        runtime["last_checks"]["ai"] = {
                            "ok": False,
                            "message": str(exc),
                            "paused_until": runtime["ai_paused_until"],
                            "checked_at": _now(),
                        }
                    logger.error("Failed to parse %s, AI cooldown=%s: %s", msg_id, cooldown, exc)
                except Exception as exc:
                    await Database.mark_parse_failed(msg_id, exc)
                    logger.error("Failed to parse %s, will retry later: %s", msg_id, exc)

                await asyncio.sleep(AI_MESSAGE_DELAY_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            runtime["last_checks"]["ai"] = {
                "ok": False,
                "message": str(exc),
                "checked_at": _now(),
            }
            logger.error("Error in background AI loop: %s", exc)

        await asyncio.sleep(AI_LOOP_INTERVAL_SECONDS)


async def watchdog_processor(client: MaxWebsocketClient):
    last_session_probe_at = 0
    while True:
        try:
            db_ok = await Database.ping()
            runtime["last_checks"]["database"] = {"ok": db_ok, "checked_at": _now()}

            should_probe_session = (
                _now() - last_session_probe_at >= WATCHDOG_SESSION_CHECK_INTERVAL_SECONDS
                and (not client.authenticated or client.last_error)
            )
            if should_probe_session:
                result = await probe_session(client.device_id, client.token)
                last_session_probe_at = _now()
                runtime["last_checks"]["session"] = {
                    "ok": result.ok,
                    "error": result.error,
                    "message": result.message,
                    "checked_at": last_session_probe_at,
                }
                if result.invalid_session:
                    client.last_error = f"{result.error}: {result.message or ''}".strip()
                    logger.critical("MAX session became invalid: %s", client.last_error)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            runtime["last_checks"]["watchdog"] = {
                "ok": False,
                "message": str(exc),
                "checked_at": _now(),
            }
            logger.error("Watchdog check failed: %s", exc)

        await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)


async def cron_weekly_report(queue):
    client = runtime.get("client")
    target_chat_id = getattr(client, "target_chat_id", None) if client else None

    class TargetQueue:
        def __init__(self, message_queue, destination):
            self._message_queue = message_queue
            self._destination = destination

        async def put(self, text, chat_id=None):
            destination = chat_id if chat_id is not None else self._destination
            await self._message_queue.put(text, chat_id=destination)

    class DummyClient:
        def __init__(self, message_queue, destination):
            self.queue = message_queue
            self.runtime_settings = runtime.get("settings")
            self.target_chat_id = destination

    dummy = DummyClient(TargetQueue(queue, target_chat_id), target_chat_id)
    await cmd_parse_finance(dummy, "\u043d\u0435\u0434\u0435\u043b\u044f", 0)
    await cmd_stata(dummy, "", 0)


def register_commands(dispatcher: Dispatcher):
    for alias in COMMAND_ALIASES_PING:
        dispatcher.register_command(alias, cmd_ping)
    for alias in COMMAND_ALIASES_STATS:
        dispatcher.register_command(alias, cmd_stata)
    for alias in COMMAND_ALIASES_PARSE_FINANCE:
        dispatcher.register_command(alias, cmd_parse_finance)
    for alias in COMMAND_ALIASES_ASK_AI:
        dispatcher.register_command(alias, cmd_ask_ai)
    for alias in COMMAND_ALIASES_HISTORY:
        dispatcher.register_command(alias, cmd_history)
    for alias in COMMAND_ALIASES_HELP:
        dispatcher.register_command(alias, cmd_help)
    for alias in COMMAND_ALIASES_STATUS:
        dispatcher.register_command(alias, cmd_status)
    for alias in COMMAND_ALIASES_CHECKS:
        dispatcher.register_command(alias, cmd_checks)
    for alias in COMMAND_ALIASES_SETUP:
        dispatcher.register_command(alias, cmd_setup)
    for alias in COMMAND_ALIASES_CHAT:
        dispatcher.register_bootstrap_command(alias, cmd_chat)
    for alias in COMMAND_ALIASES_CLEAR_AI:
        dispatcher.register_command(alias, cmd_clear_pending)
    for alias in COMMAND_ALIASES_ME:
        dispatcher.register_bootstrap_command(alias, cmd_me)


def default_runtime_settings():
    return {
        "target_chat_id": TARGET_CHAT_ID,
        "ai_provider": AI_PROVIDER,
        "gemini_api_key": GEMINI_API_KEY,
        "gemini_model": GEMINI_MODEL,
        "openai_api_key": OPENAI_API_KEY,
        "openai_model": OPENAI_MODEL,
        "openai_base_url": OPENAI_BASE_URL,
        "openai_wire_api": OPENAI_WIRE_API,
        "openai_reasoning_effort": OPENAI_REASONING_EFFORT,
        "disable_response_storage": DISABLE_RESPONSE_STORAGE,
        "deepseek_api_key": DEEPSEEK_API_KEY,
        "deepseek_model": DEEPSEEK_MODEL,
        "deepseek_base_url": DEEPSEEK_BASE_URL,
        "deepseek_wire_api": DEEPSEEK_WIRE_API,
        "deepseek_reasoning_effort": DEEPSEEK_REASONING_EFFORT,
        "report_day_of_week": REPORT_DAY_OF_WEEK,
        "report_hour": REPORT_HOUR,
        "report_minute": REPORT_MINUTE,
        "queue_min_delay": QUEUE_MIN_DELAY,
        "queue_max_delay": QUEUE_MAX_DELAY,
        "queue_typing_chars_per_second": QUEUE_TYPING_CHARS_PER_SECOND,
        "queue_typing_max_delay": QUEUE_TYPING_MAX_DELAY,
    }


async def main():
    logger.info("Starting MAX Polubot...")

    validation = validate_startup_config()
    for warning in validation.warnings:
        logger.warning(warning)
    if not validation.ok:
        for error in validation.errors:
            logger.critical(error)
        return

    await Database.init()
    settings = await Database.load_settings(default_runtime_settings())
    runtime["settings"] = settings

    session_source, device_id, token = load_session_credentials()
    runtime["session_source"] = session_source
    if not device_id or not token:
        logger.critical("Authentication credentials not found. Run auth.py or set SESSION_JSON.")
        await Database.close()
        return

    if not await run_startup_session_check(device_id, token):
        await Database.close()
        return

    dispatcher = Dispatcher(admin_ids=ADMIN_IDS)
    register_commands(dispatcher)
    dispatcher.set_default_handler(handle_financial_message)

    client = MaxWebsocketClient(device_id, token, dispatcher)
    runtime["client"] = client

    client.runtime_settings = settings
    client.target_chat_id = settings.get("target_chat_id")
    client.reply_chat_id = None

    async def _send_wrapper(text, chat_id=None):
        if chat_id is None:
            logger.warning("Dropping outgoing message because no destination chat is configured.")
            return
        destination = int(chat_id)
        await client.send_message(destination, text)

    queue = MessageQueue(
        send_func=_send_wrapper,
        min_delay=settings.get("queue_min_delay"),
        max_delay=settings.get("queue_max_delay"),
        max_size=QUEUE_MAX_SIZE,
        typing_chars_per_second=settings.get("queue_typing_chars_per_second"),
        typing_max_delay=settings.get("queue_typing_max_delay"),
        default_chat_id_getter=lambda: getattr(client, "reply_chat_id", None),
    )
    client.queue = queue
    runtime["queue"] = queue
    client.report_job_func = cron_weekly_report

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        cron_weekly_report,
        CronTrigger(
            day_of_week=settings.get("report_day_of_week"),
            hour=settings.get("report_hour"),
            minute=settings.get("report_minute"),
        ),
        args=[queue],
        id="weekly_finance_report",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    client.scheduler = scheduler

    queue.start()
    scheduler.start()
    ai_task = asyncio.create_task(background_ai_processor(), name="ai_processor")
    watchdog_task = asyncio.create_task(watchdog_processor(client), name="watchdog")

    config = uvicorn.Config(app, host=WEB_HOST, port=WEB_PORT, log_level="warning")
    server = uvicorn.Server(config)
    web_task = asyncio.create_task(server.serve(), name="health_server")
    logger.info("Keep-alive FastAPI server started on %s:%s.", WEB_HOST, WEB_PORT)

    try:
        await client.start()
    except SessionAuthError:
        logger.critical("MAX session is invalid or expired. Refresh SESSION_JSON with auth.py.")
    except asyncio.CancelledError:
        raise
    finally:
        runtime["shutting_down"] = True
        logger.info("Shutting down background tasks...")
        scheduler.shutdown(wait=False)
        await client.stop()
        await queue.stop()

        ai_task.cancel()
        watchdog_task.cancel()
        server.should_exit = True
        await asyncio.gather(ai_task, watchdog_task, web_task, return_exceptions=True)
        await Database.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Graceful shutdown initiated.")
