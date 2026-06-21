import asyncpg
import logging
from core.config import DATABASE_URL

logger = logging.getLogger(__name__)

class Database:
    pool = None

    @staticmethod
    async def init():
        if not DATABASE_URL:
            logger.critical("DATABASE_URL is not set!")
            raise ValueError("DATABASE_URL is not set in environment variables")
            
        logger.info("Initializing PostgreSQL Connection Pool...")
        Database.pool = await asyncpg.create_pool(DATABASE_URL)
        
        async with Database.pool.acquire() as conn:
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
        async with Database.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO messages (id, text, sender_id, timestamp) 
                VALUES ($1, $2, $3, $4) 
                ON CONFLICT (id) DO NOTHING
            """, msg_id, text, sender_id, timestamp)

    @staticmethod
    async def get_unparsed_messages():
        async with Database.pool.acquire() as conn:
            # Возвращает список объектов Record, поддерживающих доступ по ключам
            return await conn.fetch("SELECT * FROM messages WHERE is_parsed = FALSE")

    @staticmethod
    async def mark_parsed(msg_id):
        async with Database.pool.acquire() as conn:
            await conn.execute("UPDATE messages SET is_parsed = TRUE WHERE id = $1", msg_id)

    @staticmethod
    async def save_finance(message_id, category, expense, income, date):
        async with Database.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO finances (message_id, category, expense, income, date) 
                VALUES ($1, $2, $3, $4, $5)
            """, message_id, category, expense, income, date)

    @staticmethod
    async def get_stats(start_timestamp):
        async with Database.pool.acquire() as conn:
            stats = await conn.fetch("""
                SELECT category, SUM(expense) as total_expense, SUM(income) as total_income 
                FROM finances 
                WHERE date >= $1 
                GROUP BY category
            """, start_timestamp)
            
            row = await conn.fetchrow("""
                SELECT SUM(expense) as exp, SUM(income) as inc 
                FROM finances 
                WHERE date >= $1
            """, start_timestamp)
            
            exp = 0
            inc = 0
            if row:
                exp = row['exp'] or 0
                inc = row['inc'] or 0
                
            return stats, exp, inc
