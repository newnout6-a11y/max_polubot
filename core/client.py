import asyncio
import json
import logging
import random
import time

from curl_cffi.requests import AsyncSession

from core.config import (
    MAX_ACCEPT_LANGUAGE,
    MAX_APP_VERSION,
    MAX_BACKOFF_INITIAL_SECONDS,
    MAX_BACKOFF_MAX_SECONDS,
    MAX_DEVICE_LOCALE,
    MAX_DEVICE_NAME,
    MAX_DEVICE_TYPE,
    MAX_IMPERSONATE,
    MAX_KEEPALIVE_INTERVAL_SECONDS,
    MAX_LOCALE,
    MAX_OS_VERSION,
    MAX_PING_INTERVAL_SECONDS,
    MAX_PROTOCOL_VERSION,
    MAX_REQUEST_TIMEOUT_SECONDS,
    MAX_SCREEN,
    MAX_TIMEZONE,
    MAX_USER_AGENT,
    MAX_WS_ORIGIN,
    MAX_WS_URL,
    SOCKS_PROXY_URL,
)

logger = logging.getLogger(__name__)


class SessionAuthError(RuntimeError):
    """Raised when MAX rejects the saved session token."""


class MaxWebsocketClient:
    """MAX WebSocket client with reconnects, bounded waits and clean shutdown."""

    def __init__(self, device_id, token, dispatcher):
        self.device_id = device_id
        self.token = token
        self.dispatcher = dispatcher
        self.seq = 0
        self.ws = None
        self._pending_requests = {}
        self._handler_tasks = set()
        self._stopping = False
        self.connected = False
        self.authenticated = False
        self.last_error = None
        self.reconnect_count = 0
        self.last_connected_at = None
        self.last_authenticated_at = None
        self.last_message_at = None
        self.last_keepalive_at = None

    def _get_seq(self):
        self.seq += 1
        return self.seq

    def _get_hello_payload(self):
        return {
            "userAgent": {
                "deviceType": MAX_DEVICE_TYPE,
                "locale": MAX_LOCALE,
                "deviceLocale": MAX_DEVICE_LOCALE,
                "osVersion": MAX_OS_VERSION,
                "deviceName": MAX_DEVICE_NAME,
                "headerUserAgent": MAX_USER_AGENT,
                "appVersion": MAX_APP_VERSION,
                "screen": MAX_SCREEN,
                "timezone": MAX_TIMEZONE,
            },
            "deviceId": self.device_id,
        }

    def _get_ws_headers(self):
        return {
            "Origin": MAX_WS_ORIGIN,
            "User-Agent": MAX_USER_AGENT,
        }

    def _get_ws_connect_kwargs(self):
        kwargs = {
            "impersonate": MAX_IMPERSONATE,
            "default_headers": False,
            "headers": self._get_ws_headers(),
            "http_version": "v1",
        }
        if SOCKS_PROXY_URL:
            kwargs["proxies"] = {"https": SOCKS_PROXY_URL, "http": SOCKS_PROXY_URL}
        return kwargs

    def _fail_pending(self, exc):
        for seq, future in list(self._pending_requests.items()):
            if not future.done():
                future.set_exception(exc)
            self._pending_requests.pop(seq, None)

    def _track_handler_task(self, task):
        self._handler_tasks.add(task)

        def _cleanup(done_task):
            self._handler_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error("Unhandled message handler error: %s", exc)

        task.add_done_callback(_cleanup)

    async def stop(self):
        self._stopping = True
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass

        for task in list(self._handler_tasks):
            task.cancel()
        if self._handler_tasks:
            await asyncio.gather(*self._handler_tasks, return_exceptions=True)

        self._fail_pending(ConnectionError("MAX client stopped"))

    def status_snapshot(self):
        return {
            "connected": self.connected,
            "authenticated": self.authenticated,
            "stopping": self._stopping,
            "pending_requests": len(self._pending_requests),
            "handler_tasks": len(self._handler_tasks),
            "reconnect_count": self.reconnect_count,
            "last_error": self.last_error,
            "last_connected_at": self.last_connected_at,
            "last_authenticated_at": self.last_authenticated_at,
            "last_message_at": self.last_message_at,
            "last_keepalive_at": self.last_keepalive_at,
        }

    async def _send(self, opcode, payload, *, require_authenticated=False):
        if self.ws is None:
            raise ConnectionError("WebSocket is not connected")
        if require_authenticated and not self.authenticated:
            raise ConnectionError("MAX session is not authenticated")

        seq = self._get_seq()
        req = {
            "seq": seq,
            "opcode": opcode,
            "payload": payload,
            "ver": MAX_PROTOCOL_VERSION,
            "cmd": 0,
        }

        future = asyncio.get_running_loop().create_future()
        self._pending_requests[seq] = future

        try:
            await self.ws.send_str(json.dumps(req, ensure_ascii=False))
            return await asyncio.wait_for(future, timeout=MAX_REQUEST_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            self._pending_requests.pop(seq, None)
            raise TimeoutError(f"MAX request timed out: opcode={opcode}, seq={seq}") from exc
        except Exception:
            self._pending_requests.pop(seq, None)
            if not future.done():
                future.cancel()
            raise

    async def _send_recv_direct(self, opcode, payload):
        if self.ws is None:
            raise ConnectionError("WebSocket is not connected")

        seq = self._get_seq()
        req = {
            "seq": seq,
            "opcode": opcode,
            "payload": payload,
            "ver": MAX_PROTOCOL_VERSION,
            "cmd": 0,
        }

        try:
            await self.ws.send_str(json.dumps(req, ensure_ascii=False))
            while True:
                raw = await asyncio.wait_for(
                    self.ws.recv_str(),
                    timeout=MAX_REQUEST_TIMEOUT_SECONDS,
                )
                packet = json.loads(raw)
                if packet.get("seq") == seq:
                    return packet
                await self._handle_packet(packet)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"MAX request timed out: opcode={opcode}, seq={seq}") from exc

    async def send_message(self, chat_id: int, text: str):
        """Send a text message through an authenticated MAX session."""
        cid = random.randint(1750000000000, 2000000000000)
        logger.debug("Sending message to %s: %s...", chat_id, text[:20])

        return await self._send(
            64,
            {
                "chatId": chat_id,
                "message": {
                    "text": text,
                    "cid": cid,
                    "elements": [],
                    "attaches": [],
                },
                "notify": True,
            },
            require_authenticated=True,
        )

    async def fetch_chat_history(
        self,
        chat_id: int,
        *,
        from_time_ms: int | None = None,
        backward: int = 100,
    ) -> list[dict]:
        """Fetch a page of chat history without sending anything to the chat."""
        payload = {
            "chatId": int(chat_id),
            "from": int(from_time_ms or time.time() * 1000),
            "forward": 0,
            "backward": int(backward),
            "backwardTime": 0,
            "forwardTime": 0,
            "getChat": False,
            "getMessages": True,
            "interactive": False,
            "itemType": "REGULAR",
        }
        response = await self._send(49, payload, require_authenticated=True)
        messages = (response.get("payload") or {}).get("messages") or []
        if isinstance(messages, dict):
            flattened = []
            for value in messages.values():
                if isinstance(value, list):
                    flattened.extend(item for item in value if isinstance(item, dict))
                elif isinstance(value, dict):
                    flattened.append(value)
            return flattened
        if isinstance(messages, list):
            return [item for item in messages if isinstance(item, dict)]
        return []

    async def start(self):
        """Run the client until stopped, reconnecting on transient failures."""
        backoff = MAX_BACKOFF_INITIAL_SECONDS

        while not self._stopping:
            try:
                await self._connect_and_listen()
                if not self._stopping:
                    raise ConnectionError("MAX WebSocket listener finished unexpectedly")
            except asyncio.CancelledError:
                self._stopping = True
                raise
            except SessionAuthError:
                self.connected = False
                self.authenticated = False
                raise
            except Exception as exc:
                self.connected = False
                self.authenticated = False
                self.last_error = str(exc)
                self.reconnect_count += 1
                jittered_backoff = backoff * random.uniform(0.8, 1.2)
                logger.error("WebSocket disconnected: %s. Reconnecting in %.1fs...", exc, jittered_backoff)
                await asyncio.sleep(jittered_backoff)
                backoff = min(MAX_BACKOFF_MAX_SECONDS, backoff * 2)

    async def _connect_and_listen(self):
        session = AsyncSession()
        ws = None
        keepalive_task = None
        ping_task = None

        try:
            ws = await session.ws_connect(MAX_WS_URL, **self._get_ws_connect_kwargs())

            if self._stopping:
                await ws.close()
                return

            self.ws = ws
            self.connected = True
            self.authenticated = False
            self.last_connected_at = int(time.time())

            logger.info("[*] Connected to MAX WebSocket (impersonate=%s)", MAX_IMPERSONATE)

            await self._send_recv_direct(6, self._get_hello_payload())
            sync_resp = await self._send_recv_direct(
                19,
                {
                    "token": self.token,
                    "interactive": True,
                    "chatsCount": 40,
                    "chatsSync": 0,
                    "contactsSync": 0,
                    "presenceSync": 0,
                    "draftsSync": 0,
                },
            )

            if "error" in sync_resp.get("payload", {}):
                error = sync_resp["payload"]["error"]
                logger.critical("MAX auth failed: %s. Refresh SESSION_JSON with auth.py.", error)
                raise SessionAuthError(str(error))

            self.authenticated = True
            self.last_error = None
            self.last_authenticated_at = int(time.time())
            await self._send_recv_direct(22, {"settings": {"user": {"HIDDEN": True}}})

            keepalive_task = asyncio.create_task(self._keepalive_loop())
            if MAX_PING_INTERVAL_SECONDS > 0:
                ping_task = asyncio.create_task(self._protocol_ping_loop())

            await self._recv_loop()

        except Exception as exc:
            logger.warning("Connection closed: %s", exc)
            raise
        finally:
            self.connected = False
            self.authenticated = False
            self.ws = None
            self._fail_pending(ConnectionError("MAX WebSocket disconnected"))
            if keepalive_task:
                keepalive_task.cancel()
                await asyncio.gather(keepalive_task, return_exceptions=True)
            if ping_task:
                ping_task.cancel()
                await asyncio.gather(ping_task, return_exceptions=True)
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass
            try:
                await session.close()
            except Exception:
                pass

    async def _keepalive_loop(self):
        """Send periodic MAX protocol pings (opcode 1) to keep the session alive."""
        while True:
            await asyncio.sleep(MAX_KEEPALIVE_INTERVAL_SECONDS)
            if self.connected:
                try:
                    await self._send(1, {"interactive": False})
                    self.last_keepalive_at = int(time.time())
                    logger.debug("MAX keepalive sent")
                except Exception as exc:
                    logger.warning("Keepalive failed: %s", exc)
                    break

    async def _protocol_ping_loop(self):
        """Send RFC 6455 protocol-level ping frames to keep proxies happy."""
        while True:
            await asyncio.sleep(MAX_PING_INTERVAL_SECONDS)
            if self.ws is not None:
                try:
                    await self.ws.ping(b"keepalive")
                    logger.debug("WebSocket protocol ping sent")
                except Exception as exc:
                    logger.warning("Protocol ping failed: %s", exc)
                    break

    async def _recv_loop(self):
        while True:
            raw = await self.ws.recv_str()
            try:
                packet = json.loads(raw)
                await self._handle_packet(packet)
            except json.JSONDecodeError:
                logger.warning("Received non-JSON WebSocket packet")
            except Exception as exc:
                logger.error("Error handling message: %s", exc)

    async def _handle_packet(self, packet):
        seq = packet.get("seq")
        payload = packet.get("payload", {})

        if seq in self._pending_requests:
            self._pending_requests[seq].set_result(packet)
            del self._pending_requests[seq]
            return

        if "message" in payload and isinstance(payload["message"], dict):
            msg = payload["message"]
            chat_id = payload.get("chatId")
            if chat_id is None:
                chat_id = msg.get("chatId")
            text = msg.get("text", "")
            sender_id = msg.get("sender", 0)
            sender_name = self._extract_sender_name(msg, payload)
            msg_id = msg.get("id", "")
            ts = msg.get("time", 0)

            if text and msg_id:
                self.last_message_at = int(time.time())
                logger.debug("Incoming message %s from chat %s", msg_id, chat_id)
                task = asyncio.create_task(
                    self.dispatcher.process_message(
                        self,
                        msg_id,
                        text,
                        sender_id,
                        ts,
                        chat_id=chat_id,
                        sender_name=sender_name,
                    )
                )
                self._track_handler_task(task)

    @staticmethod
    def _extract_sender_name(message: dict, payload: dict | None = None) -> str | None:
        candidates = [
            message.get("senderName"),
            message.get("sender_name"),
            message.get("authorName"),
            message.get("author_name"),
            message.get("fromName"),
        ]
        sender = message.get("sender")
        if isinstance(sender, dict):
            candidates.extend(
                [
                    sender.get("name"),
                    sender.get("title"),
                    sender.get("displayName"),
                    sender.get("fullName"),
                    sender.get("username"),
                ]
            )
        for container_key in ("user", "author", "from"):
            item = message.get(container_key)
            if isinstance(item, dict):
                candidates.extend(
                    [
                        item.get("name"),
                        item.get("title"),
                        item.get("displayName"),
                        item.get("fullName"),
                        item.get("username"),
                    ]
                )
        if payload:
            for container_key in ("user", "author", "from"):
                item = payload.get(container_key)
                if isinstance(item, dict):
                    candidates.extend(
                        [
                            item.get("name"),
                            item.get("title"),
                            item.get("displayName"),
                            item.get("fullName"),
                            item.get("username"),
                        ]
                    )
        for value in candidates:
            text = str(value or "").strip()
            if text:
                return text
        return None
