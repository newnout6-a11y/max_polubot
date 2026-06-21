import logging

from core.config import COMMAND_PREFIX

logger = logging.getLogger(__name__)


class Dispatcher:
    """Command router and default message handler."""

    def __init__(self, admin_ids):
        self.admin_ids = [int(item) for item in admin_ids]
        self.commands = {}
        self.bootstrap_commands = set()
        self.default_handler = None

    def normalize_trigger(self, trigger: str) -> str:
        return trigger.strip().lower().removeprefix(COMMAND_PREFIX)

    def register_command(self, trigger: str, handler):
        """Register a command handler. Prefix is optional."""
        normalized = self.normalize_trigger(trigger)
        if not normalized:
            raise ValueError("Command trigger cannot be empty")
        self.commands[normalized] = handler

    def register_bootstrap_command(self, trigger: str, handler):
        """Register an id-discovery command that is allowed while ADMIN_IDS is empty."""
        self.register_command(trigger, handler)
        self.bootstrap_commands.add(self.normalize_trigger(trigger))

    def set_default_handler(self, handler):
        """Register handler for non-command messages."""
        self.default_handler = handler

    def is_admin(self, sender_id: int) -> bool:
        if not self.admin_ids:
            return False
        return int(sender_id) in self.admin_ids

    async def process_message(
        self,
        client,
        msg_id,
        text,
        sender_id,
        timestamp,
        chat_id=None,
        sender_name=None,
    ):
        """Route a single incoming message."""
        if not text:
            return

        text_trimmed = text.strip()
        if not text_trimmed:
            return

        if text_trimmed.startswith(COMMAND_PREFIX):
            parts = text_trimmed.split(maxsplit=1)
            trigger = self.normalize_trigger(parts[0])
            args = parts[1] if len(parts) > 1 else ""

            handler = self.commands.get(trigger)
            if not handler:
                logger.info("Unknown command '%s' from user %s", trigger, sender_id)
                return

            bootstrap_allowed = not self.admin_ids and trigger in self.bootstrap_commands
            if not bootstrap_allowed and not self.is_admin(sender_id):
                logger.warning("Unauthorized command '%s' from user %s. Ignoring.", trigger, sender_id)
                return

            logger.info("Executing command '%s' from user %s", trigger, sender_id)
            previous_reply_chat_id = getattr(client, "reply_chat_id", None)
            client.reply_chat_id = int(chat_id) if chat_id is not None else None
            try:
                await handler(client, args, sender_id, {"chat_id": chat_id, "msg_id": msg_id})
            except Exception as exc:
                logger.error("Error executing command %s: %s", trigger, exc)
            finally:
                client.reply_chat_id = previous_reply_chat_id
            return

        if self.default_handler:
            try:
                await self.default_handler(
                    client,
                    msg_id,
                    text_trimmed,
                    sender_id,
                    timestamp,
                    chat_id=chat_id,
                    sender_name=sender_name,
                )
            except Exception as exc:
                logger.error("Error in default handler: %s", exc)
