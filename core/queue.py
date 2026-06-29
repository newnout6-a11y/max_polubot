import asyncio
import logging
import random
from dataclasses import dataclass

from core.config import (
    QUEUE_PUT_TIMEOUT_SECONDS,
    QUEUE_RETRY_DELAY_SECONDS,
    QUEUE_SEND_RETRIES,
    QUEUE_TYPING_CHARS_PER_SECOND,
    QUEUE_TYPING_MAX_DELAY,
)

logger = logging.getLogger(__name__)


def _split_text(text: str, max_len: int = 4000) -> list[str]:
    """Split long text into chunks on line boundaries."""
    if len(text) <= max_len:
        return [text]
    lines = text.split("\n")
    chunks = []
    current = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > max_len and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


@dataclass(frozen=True)
class QueuedMessage:
    text: str
    chat_id: int | None = None
    attempts: int = 0


class MessageQueue:
    """Rate-limited outgoing message queue with retry instead of silent drops."""

    def __init__(
        self,
        send_func,
        min_delay=3.0,
        max_delay=7.0,
        max_size=100,
        typing_chars_per_second=QUEUE_TYPING_CHARS_PER_SECOND,
        typing_max_delay=QUEUE_TYPING_MAX_DELAY,
        default_chat_id_getter=None,
    ):
        self.queue = asyncio.Queue(maxsize=max_size)
        self.send_func = send_func
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_size = max_size
        self.typing_chars_per_second = typing_chars_per_second
        self.typing_max_delay = typing_max_delay
        self.default_chat_id_getter = default_chat_id_getter
        self._worker_task = None

    def start(self):
        if not self._worker_task or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("MessageQueue worker started.")

    async def stop(self):
        if self._worker_task:
            self._worker_task.cancel()
            await asyncio.gather(self._worker_task, return_exceptions=True)
            self._worker_task = None

    async def put(self, text: str, chat_id: int | None = None):
        """Add a message to the outgoing queue. Auto-splits long messages."""
        if chat_id is None and self.default_chat_id_getter:
            chat_id = self.default_chat_id_getter()
        cid = int(chat_id) if chat_id is not None else None

        chunks = _split_text(text, 4000)
        for chunk in chunks:
            message = QueuedMessage(text=chunk, chat_id=cid)
            try:
                await asyncio.wait_for(self.queue.put(message), timeout=QUEUE_PUT_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                raise TimeoutError("Outgoing message queue is full") from exc
        logger.debug("Message queued (%d chunks). Queue size: %s", len(chunks), self.queue.qsize())

    def stats(self):
        return {
            "size": self.queue.qsize(),
            "max_size": self.max_size,
            "worker_running": bool(self._worker_task and not self._worker_task.done()),
        }

    def _typing_delay(self, text: str) -> float:
        base = max(self.min_delay, min(self.max_delay, random.gauss(
            (self.min_delay + self.max_delay) / 2,
            (self.max_delay - self.min_delay) / 4,
        )))
        typing_seconds = len(text) / self.typing_chars_per_second
        jitter = max(0.5, min(1.5, random.gauss(1.0, 0.2)))
        delay = base + (typing_seconds * jitter)
        return min(delay, self.typing_max_delay)

    async def _requeue_or_drop(self, message: QueuedMessage, error: Exception):
        if message.attempts >= QUEUE_SEND_RETRIES:
            logger.error("Dropping message after %s retries: %s", message.attempts, error)
            return

        delay = QUEUE_RETRY_DELAY_SECONDS * (message.attempts + 1)
        logger.warning(
            "Failed to send queued message, retry %s/%s in %.1fs: %s",
            message.attempts + 1,
            QUEUE_SEND_RETRIES,
            delay,
            error,
        )
        await asyncio.sleep(delay)
        await asyncio.wait_for(
            self.queue.put(
                QueuedMessage(
                    text=message.text,
                    chat_id=message.chat_id,
                    attempts=message.attempts + 1,
                )
            ),
            timeout=QUEUE_PUT_TIMEOUT_SECONDS,
        )

    async def _worker_loop(self):
        while True:
            message = await self.queue.get()
            try:
                if self.queue.qsize() > 5:
                    logger.warning(
                        "Queue is overloaded (%s messages). Pausing for 30s to avoid rate limits.",
                        self.queue.qsize(),
                    )
                    await asyncio.sleep(30)

                typing_delay = self._typing_delay(message.text)

                logger.info("Typing delay: %.2fs...", typing_delay)
                await asyncio.sleep(typing_delay)
                await self.send_func(message.text, message.chat_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._requeue_or_drop(message, exc)
            finally:
                self.queue.task_done()
