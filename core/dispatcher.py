import logging

logger = logging.getLogger(__name__)

class Dispatcher:
    """
    Роутер команд и обработчик событий.
    Реализует паттерн Command Router.
    """
    def __init__(self, admin_ids):
        self.admin_ids = [int(x) for x in admin_ids if x.strip()]
        self.commands = {}
        self.default_handler = None

    def register_command(self, trigger: str, handler):
        """Регистрирует функцию-обработчик для конкретной команды."""
        self.commands[trigger] = handler

    def set_default_handler(self, handler):
        """Регистрирует функцию для обработки сообщений, не являющихся командами (например, транзакций)."""
        self.default_handler = handler

    def is_admin(self, sender_id: int) -> bool:
        """Проверяет, является ли пользователь администратором."""
        # Если список пуст, разрешаем всем (или никому). Для безопасности лучше никому.
        if not self.admin_ids:
            return False
        return sender_id in self.admin_ids

    async def process_message(self, client, msg_id, text, sender_id, timestamp):
        """Главный метод маршрутизации."""
        if not text:
            return

        text_trimmed = text.strip()
        
        # Обработка команд
        if text_trimmed.startswith("!"):
            # Парсим саму команду и аргументы
            parts = text_trimmed.split(maxsplit=1)
            trigger = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if trigger in self.commands:
                # Проверка прав доступа для команд
                if not self.is_admin(sender_id):
                    logger.warning(f"Unauthorized command '{trigger}' from user {sender_id}. Ignoring.")
                    return
                
                logger.info(f"Executing command '{trigger}' from admin {sender_id}")
                try:
                    await self.commands[trigger](client, args, sender_id)
                except Exception as e:
                    logger.error(f"Error executing command {trigger}: {e}")
                return

        # Если это не команда, передаем в default_handler (AI parser)
        if self.default_handler:
            try:
                await self.default_handler(client, msg_id, text_trimmed, sender_id, timestamp)
            except Exception as e:
                logger.error(f"Error in default handler: {e}")
