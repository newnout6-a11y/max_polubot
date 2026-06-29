import logging
import shlex
import time
from datetime import datetime, timezone, timedelta

MOSCOW_TZ = timezone(timedelta(hours=3))

from ai.parser import (
    AIProviderError,
    ask_ai,
    is_ai_available,
    parse_financial_message,
    parse_financial_messages_batch,
)
from core.config import (
    COMMAND_PREFIX,
    AI_PARSE_BATCH_SIZE,
    HISTORY_DEFAULT_DAYS,
    HISTORY_MAX_DAYS,
    HISTORY_MAX_MESSAGES,
    HISTORY_PAGE_SIZE,
)
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


def _public_ai_error(exc: Exception) -> str:
    if isinstance(exc, AIProviderError):
        return str(exc)
    return "AI provider returned an invalid response. Check provider/model/base_url in logs."


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
    raw_days = normalized.split()[0] if normalized else ""
    raw_days = raw_days.removesuffix("\u0434").removesuffix("d")
    if raw_days.isdigit():
        days = max(1, min(int(raw_days), HISTORY_MAX_DAYS))
        return days, f"{days} \u0434\u043d."
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


async def _parse_finance_period(args: str) -> tuple[int, str]:
    period, title = _parse_stats_period(args)
    return period, title


async def cmd_parse_finance(client, args, sender_id, context=None):
    settings = getattr(client, "runtime_settings", None)
    if not is_ai_available(settings):
        await client.queue.put("AI API key is not configured.")
        return

    period, title = await _parse_finance_period(args)
    start_ts = int(time.time()) - (period * 24 * 60 * 60)
    target_chat_id = int(getattr(client, "target_chat_id", 0) or 0)
    if not target_chat_id:
        await client.queue.put(
            f"\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0437\u0430\u0434\u0430\u0439 target_chat_id: "
            f"{COMMAND_PREFIX}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 target_chat_id here"
        )
        return

    normalized_args = (args or "").strip().lower()
    reparse_all = any(
        token in normalized_args.split()
        for token in {"all", "reparse", "\u0432\u0441\u0435", "\u0432\u0441\u0451", "\u0437\u0430\u043d\u043e\u0432\u043e"}
    )
    rows = await Database.get_messages_for_period(
        start_ts,
        chat_id=target_chat_id,
        limit=500,
        only_unparsed=not reparse_all,
    )
    if not rows:
        if reparse_all:
            await client.queue.put(f"\u0417\u0430 {title} \u043d\u0435\u0442 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0445 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0439.")
        else:
            await client.queue.put(
                f"\u0417\u0430 {title} \u043d\u0435\u0442 \u043d\u043e\u0432\u044b\u0445 \u043d\u0435\u0440\u0430\u0437\u043e\u0431\u0440\u0430\u043d\u043d\u044b\u0445 "
                f"\u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0439. \u0414\u043b\u044f \u043f\u043e\u0432\u0442\u043e\u0440\u043d\u043e\u0433\u043e "
                f"\u0440\u0430\u0437\u0431\u043e\u0440\u0430: {COMMAND_PREFIX}\u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c {title} all"
            )
        return

    batch_size = max(1, int(settings.get("ai_parse_batch_size") if settings else AI_PARSE_BATCH_SIZE))
    parsed_count = 0
    tx_count = 0
    ai_calls = 0
    errors = []
    found_tx = []

    async def parse_rows_batch(batch):
        nonlocal parsed_count, tx_count, ai_calls
        if not batch:
            return
        try:
            ai_calls += 1
            parsed = await parse_financial_messages_batch(batch, settings=settings)
        except Exception as exc:
            if len(batch) > 1:
                midpoint = len(batch) // 2
                await parse_rows_batch(batch[:midpoint])
                await parse_rows_batch(batch[midpoint:])
                return
            await Database.mark_parse_failed(batch[0]["id"], exc)
            errors.append(str(exc))
            return

        for row in batch:
            transactions = parsed.get(str(row["id"]), [])
            await Database.replace_finances(row["id"], transactions, row["timestamp"])
            parsed_count += 1
            tx_count += len(transactions)
            for tx in transactions:
                expl = getattr(tx, "explanation", "") or ""
                direction = "расход" if tx.expense > 0 else "доход"
                amount = tx.expense if tx.expense > 0 else tx.income
                found_tx.append(f"- {tx.category}: {direction} {amount} — {expl}")

    for index in range(0, len(rows), batch_size):
        await parse_rows_batch(rows[index : index + batch_size])
        if len(errors) >= 3:
            break

    lines = [
        f"\u0420\u0430\u0437\u0431\u043e\u0440 \u0437\u0430 {title} \u0433\u043e\u0442\u043e\u0432.",
        f"- \u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0439: {parsed_count}/{len(rows)}",
        f"- \u0422\u0440\u0430\u043d\u0437\u0430\u043a\u0446\u0438\u0439: {tx_count}",
        f"- AI-\u0432\u044b\u0437\u043e\u0432\u043e\u0432: {ai_calls}",
    ]
    if tx_count == 0:
        lines.append("- \u0424\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u044b\u0445 \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0439 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e. AI \u043f\u0440\u043e\u0430\u043d\u0430\u043b\u0438\u0437\u0438\u0440\u043e\u0432\u0430\u043b \u0432\u0441\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f \u0438 \u043d\u0435 \u043d\u0430\u0448\u0451\u043b \u0443\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0439 \u0434\u0435\u043d\u0435\u0433.")
    elif found_tx:
        lines.append("")
        lines.append("\u041d\u0430\u0439\u0434\u0435\u043d\u043d\u044b\u0435 \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438:")
        lines.extend(found_tx[:20])
    if errors:
        lines.append(f"- \u041e\u0448\u0438\u0431\u043a\u0430 AI: {errors[0]}")
    await client.queue.put("\n".join(lines))


async def cmd_ask_ai(client, args, sender_id, context=None):
    question = (args or "").strip()
    if not question:
        await client.queue.put(f"\u0424\u043e\u0440\u043c\u0430\u0442: {COMMAND_PREFIX}ai \u0442\u0432\u043e\u0439 \u0432\u043e\u043f\u0440\u043e\u0441")
        return
    settings = getattr(client, "runtime_settings", None)
    try:
        answer = await ask_ai(question, settings=settings)
    except Exception as exc:
        logger.error("AI question failed: %s", exc)
        await client.queue.put(f"AI error: {_public_ai_error(exc)}")
        return
    await client.queue.put(answer or "<empty AI response>")


def _parse_history_days(args: str) -> int:
    raw = (args or "").strip().split()
    if not raw:
        return HISTORY_DEFAULT_DAYS
    try:
        days = int(raw[0])
    except ValueError:
        return HISTORY_DEFAULT_DAYS
    return max(1, min(days, HISTORY_MAX_DAYS))


def _parse_preview_limit(args: str, default: int = 10, maximum: int = 50) -> int:
    raw = (args or "").strip().split()
    if not raw:
        return default
    try:
        value = int(raw[0])
    except ValueError:
        return default
    return max(1, min(value, maximum))


def _format_ts(timestamp) -> str:
    try:
        return datetime.fromtimestamp(int(timestamp), tz=MOSCOW_TZ).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "unknown-time"


def _preview_text(text: str, limit: int = 180) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


async def cmd_history(client, args, sender_id, context=None):
    if not hasattr(client, "fetch_chat_history"):
        await client.queue.put("MAX history fetch is not supported by this client.")
        return

    target_chat_id = int(getattr(client, "target_chat_id", 0) or 0)
    if not target_chat_id:
        await client.queue.put(
            f"\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0437\u0430\u0434\u0430\u0439 target_chat_id: "
            f"{COMMAND_PREFIX}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 target_chat_id here"
        )
        return

    days = _parse_history_days(args)
    since_ms = int((time.time() - days * 24 * 60 * 60) * 1000)
    from_ms = int(time.time() * 1000) + 1
    seen_ids = set()
    scanned = 0
    saved = 0
    pages = 0

    while scanned < HISTORY_MAX_MESSAGES:
        page = await client.fetch_chat_history(
            target_chat_id,
            from_time_ms=from_ms,
            backward=min(HISTORY_PAGE_SIZE, HISTORY_MAX_MESSAGES - scanned),
        )
        if not page:
            break

        pages += 1
        page_oldest = None
        page_new_ids = 0
        in_range = 0

        page_sender_ids = set()
        for message in page:
            msg_id = str(message.get("id") or "")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            page_new_ids += 1

            try:
                timestamp = int(message.get("time") or 0)
            except (TypeError, ValueError):
                timestamp = 0
            if timestamp:
                page_oldest = timestamp if page_oldest is None else min(page_oldest, timestamp)

            scanned += 1
            if timestamp and timestamp < since_ms:
                continue

            in_range += 1
            sid = message.get("sender") or 0
            if sid:
                page_sender_ids.add(int(sid))

        if page_sender_ids and hasattr(client, "_fetch_user_names"):
            unknown = [sid for sid in page_sender_ids if not client._resolve_sender_name(sid)]
            if unknown:
                logger.info("Fetching names for %d unknown users", len(unknown))
                await client._fetch_user_names(unknown[:50])

        for message in page:
            msg_id = str(message.get("id") or "")
            if not msg_id or msg_id not in seen_ids:
                if msg_id:
                    seen_ids.add(msg_id)

            try:
                timestamp = int(message.get("time") or 0)
            except (TypeError, ValueError):
                timestamp = 0

            if timestamp and timestamp < since_ms:
                continue

            text = str(message.get("text") or "").strip()
            if not text:
                continue

            sender_id = message.get("sender") or 0
            sender_name = None
            if hasattr(client, "_resolve_sender_name"):
                sender_name = client._resolve_sender_name(sender_id)

            await Database.save_message(
                msg_id,
                text,
                sender_id,
                timestamp,
                chat_id=target_chat_id,
                sender_name=sender_name,
            )
            saved += 1

            if scanned >= HISTORY_MAX_MESSAGES:
                break

        if page_oldest is None or page_new_ids == 0:
            break
        if page_oldest <= since_ms:
            break
        from_ms = page_oldest - 1

    await client.queue.put(
        "\n".join(
            [
                f"\u0418\u0441\u0442\u043e\u0440\u0438\u044f \u0437\u0430 {days} \u0434\u043d. \u0441\u043a\u0430\u0447\u0430\u043d\u0430.",
                f"- \u0421\u0442\u0440\u0430\u043d\u0438\u0446: {pages}",
                f"- \u041f\u0440\u043e\u0441\u043c\u043e\u0442\u0440\u0435\u043d\u043e: {scanned}",
                f"- \u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e \u0442\u0435\u043a\u0441\u0442\u043e\u0432\u044b\u0445: {saved}",
                f"\u0414\u0430\u043b\u044c\u0448\u0435: {COMMAND_PREFIX}\u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c {days}",
            ]
        )
    )


async def cmd_messages(client, args, sender_id, context=None):
    target_chat_id = int(getattr(client, "target_chat_id", 0) or 0)
    if not target_chat_id:
        await client.queue.put(
            f"Сначала задай target_chat_id: {COMMAND_PREFIX}настройка target_chat_id here"
        )
        return

    limit = _parse_preview_limit(args)
    rows = await Database.get_recent_messages(target_chat_id, limit=limit)
    if not rows:
        await client.queue.put("В БД пока нет сохранённых сообщений.")
        return

    chat_name = ""
    if hasattr(client, "_resolve_chat_name"):
        chat_name = client._resolve_chat_name(target_chat_id) or ""

    header = f"Последние сообщения ({len(rows)})" if not chat_name else f"Последние сообщения из «{chat_name}» ({len(rows)})"
    lines = [header]
    for row in rows:
        sender_name = str(row.get("sender_name") or "").strip()
        sender_label = sender_name if sender_name else str(row["sender_id"])
        lines.append(
            f"- {_format_ts(row['timestamp'])} | {sender_label} | {_preview_text(row['text'])}"
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
        f"\u0422\u0435\u043a\u0443\u0449\u0438\u0439 chat_id:\n{chat_id}\n\n"
        f"\u0414\u043b\u044f \u0447\u0442\u0435\u043d\u0438\u044f (\u0431\u043e\u0442 \u0442\u043e\u043b\u044c\u043a\u043e \u0447\u0438\u0442\u0430\u0435\u0442):\n"
        f"{prefix}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 target_chat_id here\n\n"
        f"\u0414\u043b\u044f \u043e\u0442\u0432\u0435\u0442\u043e\u0432 \u0438 \u043e\u0442\u0447\u0451\u0442\u043e\u0432:\n"
        f"{prefix}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 report_chat_id here"
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


async def cmd_wipe(client, args, sender_id, context=None):
    result = await Database.wipe_all()
    await client.queue.put(
        f"\u0411\u0430\u0437\u0430 \u043e\u0447\u0438\u0449\u0435\u043d\u0430.\n"
        f"- \u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0439 \u0443\u0434\u0430\u043b\u0435\u043d\u043e: {result['messages']}\n"
        f"- \u0424\u0438\u043d\u0430\u043d\u0441\u043e\u0432 \u0443\u0434\u0430\u043b\u0435\u043d\u043e: {result['finances']}"
    )


async def cmd_help(client, args, sender_id, context=None):
    prefix = COMMAND_PREFIX
    text = (
        "\u041a\u043e\u043c\u0430\u043d\u0434\u044b MAX Polubot:\n"
        "\u041f\u0438\u0448\u0438 \u0438\u0445 \u0432 \u0418\u0437\u0431\u0440\u0430\u043d\u043d\u043e\u0435, \u0430 target-\u0447\u0430\u0442 \u0431\u043e\u0442 \u0442\u043e\u043b\u044c\u043a\u043e \u0447\u0438\u0442\u0430\u0435\u0442.\n"
        f"- {prefix}\u0438\u0441\u0442\u043e\u0440\u0438\u044f 10 - \u0441\u043a\u0430\u0447\u0430\u0442\u044c \u0438\u0441\u0442\u043e\u0440\u0438\u044e target-\u0447\u0430\u0442\u0430 \u0437\u0430 10 \u0434\u043d\u0435\u0439\n"
        f"- {prefix}\u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f 20 - \u043f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f target-\u0447\u0430\u0442\u0430\n"
        f"- {prefix}\u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c 10 - AI-\u0440\u0430\u0437\u0431\u043e\u0440 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0445 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0439 \u0437\u0430 10 \u0434\u043d\u0435\u0439\n"
        f"- {prefix}\u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c \u043d\u0435\u0434\u0435\u043b\u044f all - \u043f\u0435\u0440\u0435\u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c \u0432\u0441\u0451 \u0437\u0430 \u043f\u0435\u0440\u0438\u043e\u0434\n"
        f"- {prefix}\u0441\u0442\u0430\u0442\u0430 / {prefix}\u0441\u0442\u0430\u0442\u0430 30 - \u0444\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u0430\u044f \u0441\u0432\u043e\u0434\u043a\u0430\n"
        f"- {prefix}ai \u0432\u043e\u043f\u0440\u043e\u0441 - \u0441\u043f\u0440\u043e\u0441\u0438\u0442\u044c AI \u043d\u0430\u043f\u0440\u044f\u043c\u0443\u044e\n"
        f"- {prefix}\u0441\u0442\u0430\u0442\u0443\u0441 - \u0431\u044b\u0441\u0442\u0440\u044b\u0439 \u0441\u0442\u0430\u0442\u0443\u0441\n"
        f"- {prefix}\u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 - \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0435 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438\n"
        f"- {prefix}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 - \u043f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u043a\u043e\u043d\u0444\u0438\u0433; {prefix}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 key value - \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c\n"
        f"- {prefix}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 target_chat_id here - \u0437\u0430\u0434\u0430\u0442\u044c \u0447\u0430\u0442 \u0434\u043b\u044f \u0447\u0442\u0435\u043d\u0438\u044f (\u0431\u043e\u0442 \u0442\u043e\u043b\u044c\u043a\u043e \u0447\u0438\u0442\u0430\u0435\u0442)\n"
        f"- {prefix}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 report_chat_id here - \u0437\u0430\u0434\u0430\u0442\u044c \u0447\u0430\u0442 \u0434\u043b\u044f \u043e\u0442\u0432\u0435\u0442\u043e\u0432 (\u043d\u0430\u043f\u0440. \u0418\u0437\u0431\u0440\u0430\u043d\u043d\u043e\u0435)\n"
        f"- {prefix}\u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 ai_provider openai|deepseek|gemini - \u0441\u043c\u0435\u043d\u0438\u0442\u044c AI\n"
        f"- {prefix}\u043e\u0447\u0438\u0441\u0442\u0438\u0442\u044c_ai - \u043e\u0447\u0438\u0441\u0442\u0438\u0442\u044c \u043e\u0447\u0435\u0440\u0435\u0434\u044c AI-\u0440\u0430\u0437\u0431\u043e\u0440\u0430\n"
        f"- {prefix}\u0441\u0442\u0435\u0440\u0435\u0442\u044c - \u0443\u0434\u0430\u043b\u0438\u0442\u044c \u0432\u0441\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f \u0438 \u0444\u0438\u043d\u0430\u043d\u0441\u044b\n"
        f"- {prefix}\u043a\u0442\u043e\u044f - \u043f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u0442\u0432\u043e\u0439 MAX user id\n"
        f"- {prefix}\u0447\u0430\u0442 - \u043f\u043e\u043a\u0430\u0437\u0430\u0442\u044c chat_id \u044d\u0442\u043e\u0433\u043e \u0447\u0430\u0442\u0430\n"
        f"- {prefix}\u043f\u0438\u043d\u0433 - \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0441\u0442\u0430\u0442\u0443\u0441\u0430\n"
        f"- {prefix}\u0445\u0435\u043b\u043f - \u044d\u0442\u043e \u043c\u0435\u043d\u044e"
    )
    await client.queue.put(text)
