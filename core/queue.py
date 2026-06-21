import asyncio
import logging
import random

logger = logging.getLogger(__name__)

class MessageQueue:
    """
    Очередь сообщений с Rate Limiting и имитацией "человеческих" задержек.
    Гарантирует, что бот не отправит 10 сообщений за 1 секунду.
    """
    def __init__(self, send_func, min_delay=3.0, max_delay=7.0):
        self.queue = asyncio.Queue()
        self.send_func = send_func
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._worker_task = None

    def start(self):
        if not self._worker_task:
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("MessageQueue worker started.")

    def stop(self):
        if self._worker_task:
            self._worker_task.cancel()
            self._worker_task = None

    async def put(self, text: str):
        """Добавляет сообщение в очередь на отправку."""
        await self.queue.put(text)
        logger.debug(f"Message queued. Queue size: {self.queue.qsize()}")

    async def _worker_loop(self):
        while True:
            try:
                # Берем сообщение из очереди
                text = await self.queue.get()
                
                # Если накопился спам, делаем жесткую паузу (Анти-бан)
                if self.queue.qsize() > 5:
                    logger.warning(f"Queue is overloaded ({self.queue.qsize()} msgs). Pausing for 30s to avoid ban.")
                    await asyncio.sleep(30)
                
                # Имитация задержки печатания
                typing_delay = len(text) / 5.0 + random.uniform(self.min_delay, self.max_delay)
                # Ограничиваем максимальную задержку, чтобы не ждать вечно
                typing_delay = min(typing_delay, 15.0) 
                
                logger.info(f"Typing delay: {typing_delay:.2f}s...")
                await asyncio.sleep(typing_delay)
                
                # Отправляем
                await self.send_func(text)
                
                # Помечаем задачу как выполненную
                self.queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in MessageQueue worker: {e}")
                # Ждем перед следующей попыткой, чтобы не спамить лог
                await asyncio.sleep(2)
