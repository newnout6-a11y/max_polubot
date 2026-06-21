import logging
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from core.config import (
    DATABASE_URL,
    AI_MAX_PARSE_ATTEMPTS,
    AI_RETRY_BASE_SECONDS,
    AI_RETRY_MAX_SECONDS,
    DB_CONNECT_TIMEOUT_SECONDS,
    DB_POOL_MAX_SIZE,
    DB_POOL_MIN_SIZE,
    DB_SSLMODE,
)
from core.settings import SETTINGS, RuntimeSettings

logger = logging.getLogger(__name__)


def _build_conninfo() -> str:
    if not DATABASE_URL:
        return ""
    parsed = urlsplit(DATABASE_URL)
    if not parsed.scheme.startswith("postgres"):
        return DATABASE_URL

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("sslmode", DB_SSLMODE)
    query.setdefault("connect_timeout", str(DB_CONNECT_TIMEOUT_SECONDS))
    return urlunsplit(parsed._replace(query=urlencode(query)))


def normalize_unix_timestamp(value) -> int:
    """Return seconds since epoch even if MAX sends milliseconds."""
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return int(time.time())

    if timestamp <= 0:
        return int(time.time())
    if timestamp > 10_000_000_000:
        return timestamp // 1000
    return timestamp


class Database:
    pool = None

    @staticmethod
    def _require_pool():
        if Database.pool is None:
            raise RuntimeError("Database pool is not initialized")
        return Database.pool

    @staticmethod
    async def init():
        if not DATABASE_URL:
            logger.critical("DATABASE_URL is not set!")
            raise ValueError("DATABASE_URL is not set in environment variables")

        logger.info("Initializing PostgreSQL connection pool...")
        Database.pool = AsyncConnectionPool(
            conninfo=_build_conninfo(),
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            open=False,
            kwargs={"row_factory": dict_row},
        )
        await Database.pool.open()

        async with Database.pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    chat_id BIGINT,
                    sender_id BIGINT,
                    timestamp BIGINT NOT NULL,
                    is_parsed BOOLEAN DEFAULT FALSE,
                    parse_attempts INTEGER DEFAULT 0,
                    next_parse_at BIGINT DEFAULT 0,
                    last_error TEXT,
                    parsed_at BIGINT,
                    updated_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
                )
                """
            )
            await conn.execute(
                """
                ALTER TABLE messages
                    ADD COLUMN IF NOT EXISTS chat_id BIGINT,
                    ADD COLUMN IF NOT EXISTS parse_attempts INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS next_parse_at BIGINT DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS last_error TEXT,
                    ADD COLUMN IF NOT EXISTS parsed_at BIGINT,
                    ADD COLUMN IF NOT EXISTS updated_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS finances (
                    id SERIAL PRIMARY KEY,
                    message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    expense INTEGER DEFAULT 0,
                    income INTEGER DEFAULT 0,
                    date BIGINT NOT NULL
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_unparsed ON messages (is_parsed, next_parse_at, timestamp)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_finances_date_category ON finances (date, category)"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    is_secret BOOLEAN DEFAULT FALSE,
                    updated_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
                )
                """
            )
            logger.info("Database tables and indexes verified.")

    @staticmethod
    async def close():
        if Database.pool:
            await Database.pool.close()
            Database.pool = None
            logger.info("Database connection pool closed.")

    @staticmethod
    async def ping() -> bool:
        pool = Database._require_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1 AS ok")
                row = await cur.fetchone()
                return bool(row and row["ok"] == 1)

    @staticmethod
    async def save_message(msg_id, text, sender_id, timestamp, chat_id=None):
        pool = Database._require_pool()
        normalized_ts = normalize_unix_timestamp(timestamp)
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO messages (id, text, chat_id, sender_id, timestamp, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    text = EXCLUDED.text,
                    chat_id = EXCLUDED.chat_id,
                    sender_id = EXCLUDED.sender_id,
                    timestamp = EXCLUDED.timestamp,
                    updated_at = EXCLUDED.updated_at
                WHERE messages.is_parsed = FALSE
                """,
                (msg_id, text, chat_id, sender_id, normalized_ts, int(time.time())),
            )

    @staticmethod
    async def get_messages_for_period(
        start_timestamp,
        end_timestamp=None,
        chat_id=None,
        limit=500,
        only_unparsed=False,
    ):
        pool = Database._require_pool()
        normalized_start = normalize_unix_timestamp(start_timestamp)
        normalized_end = normalize_unix_timestamp(end_timestamp or int(time.time()))
        clauses = ["timestamp >= %s", "timestamp <= %s"]
        params = [normalized_start, normalized_end]
        if chat_id:
            clauses.append("chat_id = %s")
            params.append(int(chat_id))
        if only_unparsed:
            clauses.append("is_parsed = FALSE")
        params.append(limit)

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT *
                    FROM messages
                    WHERE {" AND ".join(clauses)}
                    ORDER BY timestamp ASC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                return await cur.fetchall()

    @staticmethod
    async def get_unparsed_messages(limit=20):
        pool = Database._require_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT *
                    FROM messages
                    WHERE is_parsed = FALSE
                      AND parse_attempts < %s
                      AND COALESCE(next_parse_at, 0) <= %s
                    ORDER BY timestamp ASC
                    LIMIT %s
                    """,
                    (AI_MAX_PARSE_ATTEMPTS, int(time.time()), limit),
                )
                return await cur.fetchall()

    @staticmethod
    async def mark_parsed(msg_id):
        pool = Database._require_pool()
        async with pool.connection() as conn:
            await conn.execute(
                """
                UPDATE messages
                SET is_parsed = TRUE,
                    last_error = NULL,
                    next_parse_at = 0,
                    parsed_at = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (int(time.time()), int(time.time()), msg_id),
            )

    @staticmethod
    async def mark_parse_failed(msg_id, error, retry_after_seconds: float | None = None):
        pool = Database._require_pool()
        now = int(time.time())
        if retry_after_seconds is None:
            retry_after_seconds = AI_RETRY_BASE_SECONDS
        retry_after = min(int(retry_after_seconds), int(AI_RETRY_MAX_SECONDS))
        next_parse_at = now + retry_after
        async with pool.connection() as conn:
            await conn.execute(
                """
                UPDATE messages
                SET parse_attempts = parse_attempts + 1,
                    last_error = %s,
                    next_parse_at = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (
                    str(error)[:1000],
                    next_parse_at,
                    now,
                    msg_id,
                ),
            )

    @staticmethod
    async def save_finance(message_id, category, expense, income, date):
        pool = Database._require_pool()
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO finances (message_id, category, expense, income, date)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (message_id, category, expense, income, normalize_unix_timestamp(date)),
            )

    @staticmethod
    async def replace_finances(message_id, transactions, date):
        pool = Database._require_pool()
        normalized_date = normalize_unix_timestamp(date)
        now = int(time.time())

        async with pool.connection() as conn:
            await conn.execute("DELETE FROM finances WHERE message_id = %s", (message_id,))
            for transaction in transactions:
                await conn.execute(
                    """
                    INSERT INTO finances (message_id, category, expense, income, date)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        message_id,
                        transaction.category,
                        transaction.expense,
                        transaction.income,
                        normalized_date,
                    ),
                )
            await conn.execute(
                """
                UPDATE messages
                SET is_parsed = TRUE,
                    last_error = NULL,
                    parsed_at = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (now, now, message_id),
            )

    @staticmethod
    async def get_stats(start_timestamp):
        pool = Database._require_pool()
        normalized_start = normalize_unix_timestamp(start_timestamp)

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        category,
                        COALESCE(SUM(expense), 0) AS total_expense,
                        COALESCE(SUM(income), 0) AS total_income
                    FROM finances
                    WHERE date >= %s
                    GROUP BY category
                    ORDER BY total_expense DESC, total_income DESC, category ASC
                    """,
                    (normalized_start,),
                )
                stats = await cur.fetchall()

            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        COALESCE(SUM(expense), 0) AS exp,
                        COALESCE(SUM(income), 0) AS inc
                    FROM finances
                    WHERE date >= %s
                    """,
                    (normalized_start,),
                )
                row = await cur.fetchone()

            exp = row["exp"] if row else 0
            inc = row["inc"] if row else 0
            return stats, exp, inc

    @staticmethod
    async def get_operational_stats():
        pool = Database._require_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE is_parsed = FALSE) AS unparsed,
                        COUNT(*) FILTER (WHERE is_parsed = FALSE AND parse_attempts > 0) AS retrying,
                        COUNT(*) FILTER (
                            WHERE is_parsed = FALSE
                              AND parse_attempts > 0
                              AND COALESCE(next_parse_at, 0) > EXTRACT(EPOCH FROM NOW())::BIGINT
                        ) AS retry_delayed,
                        COUNT(*) FILTER (
                            WHERE is_parsed = FALSE
                              AND parse_attempts >= %s
                        ) AS retry_exhausted,
                        COUNT(*) FILTER (WHERE is_parsed = TRUE) AS parsed,
                        COALESCE(MAX(parse_attempts), 0) AS max_parse_attempts
                    FROM messages
                    """,
                    (AI_MAX_PARSE_ATTEMPTS,),
                )
                row = await cur.fetchone()

            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, parse_attempts, next_parse_at, last_error
                    FROM messages
                    WHERE is_parsed = FALSE AND last_error IS NOT NULL
                    ORDER BY updated_at DESC
                    LIMIT 3
                    """
                )
                recent_errors = await cur.fetchall()

        return {
            "unparsed": row["unparsed"] if row else 0,
            "retrying": row["retrying"] if row else 0,
            "retry_delayed": row["retry_delayed"] if row else 0,
            "retry_exhausted": row["retry_exhausted"] if row else 0,
            "parsed": row["parsed"] if row else 0,
            "max_parse_attempts": row["max_parse_attempts"] if row else 0,
            "recent_parse_errors": [
                {
                    "id": item["id"],
                    "parse_attempts": item["parse_attempts"],
                    "next_parse_at": item["next_parse_at"],
                    "last_error": item["last_error"],
                }
                for item in recent_errors
            ],
        }

    @staticmethod
    async def load_settings(defaults: dict[str, object]) -> RuntimeSettings:
        pool = Database._require_pool()
        values = dict(defaults)
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT key, value FROM app_settings")
                rows = await cur.fetchall()

        for row in rows:
            key = row["key"]
            if key not in SETTINGS:
                continue
            try:
                values[key] = SETTINGS[key].parse(row["value"])
            except Exception as exc:
                logger.warning("Ignoring invalid stored setting %s: %s", key, exc)
        return RuntimeSettings(values)

    @staticmethod
    async def save_setting(key: str, value):
        pool = Database._require_pool()
        spec = SETTINGS[key]
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, is_secret, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    is_secret = EXCLUDED.is_secret,
                    updated_at = EXCLUDED.updated_at
                """,
                (key, str(value), spec.secret, int(time.time())),
            )

    @staticmethod
    async def clear_unparsed_messages() -> int:
        pool = Database._require_pool()
        async with pool.connection() as conn:
            result = await conn.execute(
                """
                DELETE FROM messages
                WHERE is_parsed = FALSE
                """
            )
            return result.rowcount or 0
