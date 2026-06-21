import logging

from db.models import Database

logger = logging.getLogger(__name__)


async def handle_financial_message(client, msg_id, text, sender_id, timestamp, chat_id=None):
    """Store target-chat messages; AI parsing is started by command or report."""
    target_chat_id = int(getattr(client, "target_chat_id", 0) or 0)
    if not target_chat_id:
        logger.debug("Ignoring non-command message %s: target_chat_id is not configured.", msg_id)
        return
    if chat_id is None or int(chat_id) != target_chat_id:
        logger.debug(
            "Ignoring non-command message %s from chat %s: target_chat_id is %s.",
            msg_id,
            chat_id,
            target_chat_id,
        )
        return

    await Database.save_message(msg_id, text, sender_id, timestamp, chat_id=chat_id)
    logger.info("Saved message %s from target chat.", msg_id)
