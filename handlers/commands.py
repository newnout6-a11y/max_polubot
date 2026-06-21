import logging
import shlex
import time

from ai.parser import AIProviderError, is_ai_available, parse_financial_message
from core.config import COMMAND_PREFIX
from core.session_probe import probe_session
from core.settings import (
    SETTINGS,
    format_settings,
    mask_value,
    normalize_key,
    parse_setting_value,
)
from db.models import Database

logger = logging.getLogger(__name__)


def _yes_no(value: bool) -> str:
    return "\u0434\u0430" if value else "\u043d\u0435\u0442"


async def cmd_ping(client, args, sender_id, context=None):
    await client.queue.put("\u041f\u043e\u043d\u0433! \u0411\u043e\u0442 \u043d\u0430 \u0441\u0432\u044f\u0437\u0438.")


def _parse_stats_period(args: str) -> tuple[int, str]:
    normalized = (args or "").strip().lower()
    if normalized in {"\u043c\u0435\u0441\u044f\u0446", "\u043c\u0435\u0441", "30", "30\u0434", "30d", "month"}:
        return 30, "\u043c\u0435\u0441\u044f\u0446"
    if normalized in {"\u0434\u0435\u043d\u044c", "1", "1\u0434", "1d", "day"}:
        return 1, "\u0434\u0435\u043d\u044c"
    return 7, "\u043d\u0435\u0434\u0435\u043b\u044e"


async def cmd_stata(client, args, sender_id, context=None):
    period, title = _parse_stats_period(args)
    start_ts = int(time.time()) - (period * 24 * 60 * 60)

    stats, total_exp, total_inc = await Database.get_stats(start_ts)

    if not stats and total_exp == 0 and total_inc == 0:
        await client.queue.put(f"\u0417\u0430 {title} \u043d\u0435\u0442 \u043d\u0438 \u043e\u0434\u043d\u043e\u0439 \u0437\u0430\u043f\u0438\u0441\u0438.")
        return

    lines = [f"\u0424\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u0430\u044f \u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430 \u0437\u0430 {title}", ""]
    for row in stats:
        cat = str(row["category"]).capitalize()
        exp = row["total_expense"] or 0
        inc = row["total_income"] or 0
        parts = [f"- {cat}:"]
        if exp > 0:
            parts.append(f"\u0440\u0430\u0441\u0445\u043e\u0434 {exp}")
        if inc > 0:
            parts.append(f"\u0434\u043e\u0445\u043e\u0434 {inc}")
        lines.append(" ".join(parts))

    lines.extend(
        [
            "",
            f"\u0420\u0430\u0441\u0445\u043e\u0434: {total_exp}",
            f"\u0414\u043e\u0445\u043e\u0434: {total_inc}",
            f"\u0414\u0435\u043b\u044c\u0442\u0430: {total_inc - total_exp}",
        ]
    )
    await client.queue.put("\n".join(lines))


async def cmd_status(client, args, sender_id, context=None):
    client_status = client.status_snapshot() if hasattr(client, "status_snapshot") else {}
    queue_stats = client.queue.stats() if getattr(client, "queue", None) else {}
    settings = getattr(client, "runtime_settings", None)

    try:
        db_ok = await Database.ping()
        db_stats = await Database.get_operational_stats()
    except Exception as exc:
        db_ok = False
        db_stats = {"error": str(exc)}

    lines = [
        "\u0421\u0442\u0430\u0442\u0443\u0441 MAX Polubot",
        f"- MAX connected: {_yes_no(client_status.get('connected', False))}",
        f"- MAX authenticated: {_yes_no(client_status.get('authenticated', False))}",
        f"- DB: {_yes_no(db_ok)}",
        f"- AI: {_yes_no(is_ai_available(settings))}",
        f"- AI provider: {settings.get('ai_provider') if settings else '?'}",
        f"- Queue: {queue_stats.get('size', 0)}/{queue_stats.get('max_size', '?')}",
        f"- Queue worker: {_yes_no(queue_stats.get('worker_running', False))}",
        f"- Reconnects: {client_status.get('reconnect_count', 0)}",
        f"- Pending MAX requests: {client_status.get('pending_requests', 0)}",
        f"- Unparsed messages: {db_stats.get('unparsed', 0)}",
        f"- Retrying parses: {db_stats.get('retrying', 0)}",
        f"- Delayed retries: {db_stats.get('retry_delayed', 0)}",
        f"- Exhausted retries: {db_stats.get('retry_exhausted', 0)}",
    ]

    last_error = client_status.get("last_error")
    if last_error:
        lines.append(f"- Last MAX error: {last_error}")
    if db_stats.get("error"):
        lines.append(f"- DB error: {db_stats['error']}")

    await client.queue.put("\n".join(lines))


async def cmd_checks(client, args, sender_id, context=None):
    settings = getattr(client, "runtime_settings", None)
    lines = ["\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 MAX Polubot"]

    try:
        db_ok = await Database.ping()
        lines.append(f"- DB ping: {_yes_no(db_ok)}")
    except Exception as exc:
        lines.append(f"- DB ping: \u043d\u0435\u0442 ({exc})")

    result = await probe_session(client.device_id, client.token)
    if result.ok:
        lines.append("- MAX session: OK")
    else:
        lines.append(f"- MAX session: FAIL ({result.error}: {result.message or ''})")

    if not is_ai_available(settings):
        lines.append("- AI: \u043d\u0435\u0442 (API key is missing)")
    else:
        try:
            await parse_financial_message("\u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 0", settings=settings)
            lines.append("- AI request: OK")
        except AIProviderError as exc:
            lines.append(f"- AI request: FAIL ({exc})")
        except Exception as exc:
            lines.append(f"- AI request: FAIL ({exc})")
    await client.queue.put("\n".join(lines))


async def cmd_chat(client, args, sender_id, context=None):
    chat_id = (context or {}).get("chat_id")
    if not chat_id:
        await client.queue.put("\u041d\u0435 \u0432\u0438\u0436\u0443 chat_id \u0432 \u0442\u0435\u043a\u0443\u0449\u0435\u043c \u0441\u043e\u0431\u044b\u0442\u0438\u0438.")
        return

    prefix = COMMAND_PREFIX
    await client.queue.put(
        "\u0422\u0435\u043a\u0443\u0449\u0438\u0439 chat_id:\n"
        f"{chat_id}\n\n"
        "\u0427\u0442\u043e\u0431\u044b \u0437\u0430\u043a\u0440\u0435\u043f\u0438\u0442\u044c \u044d\u0442\u043e\u0442 \u0447\u0430\u0442 "
        "\u0434\u043b\u044f \u043e\u0442\u0432\u0435\u0442\u043e\u0432 \u0438 \u043e\u0442\u0447\u0451\u0442\u043e\u0432:\n"
        f"{prefix}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 target_chat_id here"
    )


async def cmd_me(client, args, sender_id, context=None):
    chat_id = (context or {}).get("chat_id") or ""
    lines = [
        "\u0422\u0432\u043e\u0439 MAX user id:",
        str(sender_id),
        "",
        "\u0414\u043b\u044f Hugging Face Secrets:",
        f"ADMIN_IDS={sender_id}",
    ]
    if chat_id:
        lines.extend(["", "\u0422\u0435\u043a\u0443\u0449\u0438\u0439 chat_id:", str(chat_id)])
    await client.queue.put("\n".join(lines))


async def cmd_setup(client, args, sender_id, context=None):
    settings = getattr(client, "runtime_settings", None)
    if settings is None:
        await client.queue.put("Runtime settings are not initialized.")
        return

    text = (args or "").strip()
    if not text or text.lower() in {"show", "list", "\u043f\u043e\u043a\u0430\u0437\u0430\u0442\u044c"}:
        await client.queue.put(format_settings(settings))
        return

    try:
        parts = shlex.split(text)
    except ValueError as exc:
        await client.queue.put(f"\u041e\u0448\u0438\u0431\u043a\u0430 \u0440\u0430\u0437\u0431\u043e\u0440\u0430: {exc}")
        return

    if parts and parts[0].lower() == "set":
        parts = parts[1:]
    if len(parts) < 2:
        await client.queue.put(
            "\u0424\u043e\u0440\u043c\u0430\u0442: "
            f"{COMMAND_PREFIX}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 key value\n"
            f"\u041f\u0440\u0438\u043c\u0435\u0440: {COMMAND_PREFIX}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 ai_provider openai"
        )
        return

    raw_key = parts[0]
    raw_value = " ".join(parts[1:])

    try:
        key = normalize_key(raw_key)
        value = parse_setting_value(key, raw_value, context=context or {})
        settings.set(key, value)
        await Database.save_setting(key, value)
        await _apply_setting_side_effects(client, key, value)
    except Exception as exc:
        await client.queue.put(f"\u041d\u0435 \u0441\u043c\u043e\u0433 \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c: {exc}")
        return

    shown = mask_value(value) if SETTINGS[key].secret else value
    await client.queue.put(f"\u0413\u043e\u0442\u043e\u0432\u043e: {key} = {shown}")


async def cmd_clear_pending(client, args, sender_id, context=None):
    deleted = await Database.clear_unparsed_messages()
    await client.queue.put(f"\u041e\u0447\u0435\u0440\u0435\u0434\u044c AI \u043e\u0447\u0438\u0449\u0435\u043d\u0430: {deleted}")


async def _apply_setting_side_effects(client, key, value):
    if key == "target_chat_id":
        client.target_chat_id = int(value)
    if key == "queue_min_delay":
        client.queue.min_delay = float(value)
    if key == "queue_max_delay":
        client.queue.max_delay = float(value)
    if key == "queue_typing_chars_per_second":
        client.queue.typing_chars_per_second = float(value)
    if key == "queue_typing_max_delay":
        client.queue.typing_max_delay = float(value)

    scheduler = getattr(client, "scheduler", None)
    if scheduler and key in {"report_day_of_week", "report_hour", "report_minute"}:
        from apscheduler.triggers.cron import CronTrigger

        settings = client.runtime_settings
        report_job = getattr(client, "report_job_func", None)
        if report_job is None:
            return
        scheduler.add_job(
            report_job,
            CronTrigger(
                day_of_week=settings.get("report_day_of_week"),
                hour=settings.get("report_hour"),
                minute=settings.get("report_minute"),
            ),
            args=[client.queue],
            id="weekly_finance_report",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )


async def cmd_help(client, args, sender_id, context=None):
    prefix = COMMAND_PREFIX
    text = (
        "\u041a\u043e\u043c\u0430\u043d\u0434\u044b MAX Polubot:\n"
        f"- {prefix}\u0441\u0442\u0430\u0442\u0430 - \u0441\u0432\u043e\u0434\u043a\u0430 \u0437\u0430 7 \u0434\u043d\u0435\u0439\n"
        f"- {prefix}\u0441\u0442\u0430\u0442\u0430 \u043c\u0435\u0441\u044f\u0446 - \u0441\u0432\u043e\u0434\u043a\u0430 \u0437\u0430 30 \u0434\u043d\u0435\u0439\n"
        f"- {prefix}\u0441\u0442\u0430\u0442\u0443\u0441 - \u0431\u044b\u0441\u0442\u0440\u044b\u0439 \u0441\u0442\u0430\u0442\u0443\u0441\n"
        f"- {prefix}\u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 - \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0435 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438\n"
        f"- {prefix}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 - \u043f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u043a\u043e\u043d\u0444\u0438\u0433\n"
        f"- {prefix}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 ai_provider openai|deepseek|gemini\n"
        f"- {prefix}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 target_chat_id here\n"
        f"- {prefix}\u043e\u0447\u0438\u0441\u0442\u0438\u0442\u044c_ai - \u0443\u0434\u0430\u043b\u0438\u0442\u044c \u043e\u0436\u0438\u0434\u0430\u044e\u0449\u0438\u0435 AI-\u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f\n"
        f"- {prefix}\u043a\u0442\u043e\u044f - \u043f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u0442\u0432\u043e\u0439 MAX user id\n"
        f"- {prefix}\u0447\u0430\u0442 - \u043f\u043e\u043a\u0430\u0437\u0430\u0442\u044c chat_id \u044d\u0442\u043e\u0433\u043e \u0447\u0430\u0442\u0430\n"
        f"- {prefix}\u043f\u0438\u043d\u0433 - \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0441\u0442\u0430\u0442\u0443\u0441\u0430\n"
        f"- {prefix}\u0445\u0435\u043b\u043f - \u044d\u0442\u043e \u043c\u0435\u043d\u044e"
    )
    await client.queue.put(text)
