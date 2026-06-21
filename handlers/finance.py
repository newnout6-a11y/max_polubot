import logging
from db.models import Database
# The ai parser is now in ai.parser

logger = logging.getLogger(__name__)

async def handle_financial_message(client, msg_id, text, sender_id, timestamp, chat_id=None):
    """
    Обработчик по умолчанию для текстовых сообщений.
    Только сохраняет текст в базу. AI будет обрабатывать их асинхронно отдельным воркером,
    чтобы не блокировать WebSocket listener.
    """
    target_chat_id = int(getattr(client, "target_chat_id", 0) or 0)
    if not target_chat_id:
        logger.info("Ignoring non-command message %s: target_chat_id is not configured.", msg_id)
        return
    if not chat_id or int(chat_id) != target_chat_id:
        logger.info(
            "Ignoring non-command message %s from chat %s: target_chat_id is %s.",
            msg_id,
            chat_id,
            target_chat_id,
        )
        return

    await Database.save_message(msg_id, text, sender_id, timestamp)
    logger.info("Saved message %s from target chat for background AI processing.", msg_id)
