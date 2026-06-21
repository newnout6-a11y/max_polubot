import logging
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from core.config import DATABASE_URL

logger = logging.getLogger(__name__)

class Database:
    pool = None

    @staticmethod
    async def init():
        if not DATABASE_URL:
            logger.critical("DATABASE_URL is not set!")
            raise ValueError("DATABASE_URL is not set in environment variables")
            
        logger.info("Initializing PostgreSQL Connection Pool (psycopg3)...")
        # Инициализируем пул подключений с фабрикой строк dict_row для доступа row['field']
        Database.pool = AsyncConnectionPool(
            conninfo=DATABASE_URL,
            open=False,
            kwargs={"row_factory": dict_row}
        )
        await Database.pool.open()
        
        async with Database.pool.connection() as conn:
            # Инициализация таблиц
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    text TEXT,
                    sender_id BIGINT,
                    timestamp BIGINT,
                    is_parsed BOOLEAN DEFAULT FALSE
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS finances (
                    id SERIAL PRIMARY KEY,
                    message_id TEXT REFERENCES messages(id),
                    category TEXT,
                    expense INTEGER DEFAULT 0,
                    income INTEGER DEFAULT 0,
                    date BIGINT
                )
            """)
            logger.info("Database tables verified.")

    @staticmethod
    async def save_message(msg_id, text, sender_id, timestamp):
        async with Database.pool.connection() as conn:
            await conn.execute("""
                INSERT INTO messages (id, text, sender_id, timestamp) 
                VALUES (%s, %s, %s, %s) 
                ON CONFLICT (id) DO NOTHING
            """, (msg_id, text, sender_id, timestamp))

    @staticmethod
    async def get_unparsed_messages():
        async with Database.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT * FROM messages WHERE is_parsed = FALSE")
                return await cur.fetchall()

    @staticmethod
    async def mark_parsed(msg_id):
        async with Database.pool.connection() as conn:
            await conn.execute("UPDATE messages SET is_parsed = TRUE WHERE id = %s", (msg_id,))

    @staticmethod
    async def save_finance(message_id, category, expense, income, date):
        async with Database.pool.connection() as conn:
            await conn.execute("""
                INSERT INTO finances (message_id, category, expense, income, date) 
                VALUES (%s, %s, %s, %s, %s)
            """, (message_id, category, expense, income, date))

    @staticmethod
    async def get_stats(start_timestamp):
        async with Database.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT category, SUM(expense) as total_expense, SUM(income) as total_income 
                    FROM finances 
                    WHERE date >= %s 
                    GROUP BY category
                """, (start_timestamp,))
                stats = await cur.fetchall()
            
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT SUM(expense) as exp, SUM(income) as inc 
                    FROM finances 
                    WHERE date >= %s
                """, (start_timestamp,))
                row = await cur.fetchone()
            
            exp = 0
            inc = 0
            if row:
                exp = row['exp'] or 0
                inc = row['inc'] or 0
                
            return stats, exp, inc
