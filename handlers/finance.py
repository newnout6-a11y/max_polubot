import logging
from db.models import Database
# The ai parser is now in ai.parser

logger = logging.getLogger(__name__)

async def handle_financial_message(client, msg_id, text, sender_id, timestamp):
    """
    Обработчик по умолчанию для текстовых сообщений.
    Только сохраняет текст в базу. AI будет обрабатывать их асинхронно отдельным воркером,
    чтобы не блокировать WebSocket listener.
    """
    await Database.save_message(msg_id, text, sender_id, timestamp)
    logger.info(f"Saved message {msg_id} for background AI processing.")
