import asyncio
import json
import logging
import random
import websockets

logger = logging.getLogger(__name__)

class MaxWebsocketClient:
    """
    Умный WebSocket клиент для MAX.
    Поддерживает Exponential Backoff, реконнекты и эмуляцию живого браузера.
    """
    WS_URL = "wss://ws-api.oneme.ru/websocket"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

    def __init__(self, device_id, token, dispatcher):
        self.device_id = device_id
        self.token = token
        self.dispatcher = dispatcher
        self.seq = 0
        self.ws = None
        self._pending_requests = {}
        self.connected = False

    def _get_seq(self):
        self.seq += 1
        return self.seq

    def _get_hello_payload(self):
        return {
            "userAgent": {
                "deviceType": "WEB",
                "locale": "ru",
                "deviceLocale": "ru",
                "osVersion": "Windows",
                "deviceName": "Chrome",
                "headerUserAgent": self.USER_AGENT,
                "appVersion": "26.2.2",
                "screen": "1920x1080 1.0x",
                "timezone": "Europe/Moscow"
            },
            "deviceId": self.device_id
        }

    async def _send(self, opcode, payload):
        if not self.ws or not self.connected:
            raise ConnectionError("WebSocket is not connected")
        
        seq = self._get_seq()
        req = {
            "seq": seq,
            "opcode": opcode,
            "payload": payload,
            "ver": 11,
            "cmd": 0
        }
        
        future = asyncio.get_running_loop().create_future()
        self._pending_requests[seq] = future
        
        await self.ws.send(json.dumps(req))
        return await future

    async def send_message(self, chat_id: int, text: str):
        """Отправляет текстовое сообщение (вызывается из очереди)."""
        cid = random.randint(1750000000000, 2000000000000)
        logger.debug(f"Sending message to {chat_id}: {text[:20]}...")
        
        return await self._send(64, {
            "chatId": chat_id,
            "message": {
                "text": text,
                "cid": cid,
                "elements": [],
                "attaches": []
            },
            "notify": True
        })

    async def start(self):
        """Запускает клиент с Exponential Backoff при обрывах."""
        backoff = 1.0
        max_backoff = 32.0

        while True:
            try:
                await self._connect_and_listen()
                # Если вышли без исключения, значит штатная остановка
                break
            except Exception as e:
                self.connected = False
                logger.error(f"WebSocket disconnected: {e}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                # Exponential backoff
                backoff = min(max_backoff, backoff * 2)

    async def _connect_and_listen(self):
        async for ws in websockets.connect(self.WS_URL, origin="https://web.max.ru", user_agent_header=self.USER_AGENT):
            self.ws = ws
            try:
                logger.info("[*] Connected to MAX WebSocket")
                
                # 1. Hello
                await self._send(6, self._get_hello_payload())
                
                # 2. Login
                sync_resp = await self._send(19, {
                    "token": self.token,
                    "interactive": True,
                    "chatsCount": 40,
                    "chatsSync": 0,
                    "contactsSync": 0,
                    "presenceSync": 0,
                    "draftsSync": 0
                })
                
                if "error" in sync_resp.get("payload", {}):
                    logger.critical(f"Auth failed: {sync_resp['payload']['error']}")
                    return
                
                # 3. Stealth (Невидимка)
                await self._send(22, {"settings": {"user": {"HIDDEN": True}}})
                self.connected = True
                
                # Запускаем пинг
                keepalive_task = asyncio.create_task(self._keepalive_loop())
                
                # Слушаем сообщения
                await self._recv_loop()
                
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"Connection closed by server: {e}")
                raise
            finally:
                self.connected = False
                if 'keepalive_task' in locals():
                    keepalive_task.cancel()

    async def _keepalive_loop(self):
        """Пинг каждые 30 секунд для поддержания сессии."""
        while True:
            await asyncio.sleep(30)
            if self.connected:
                try:
                    await self._send(1, {"interactive": False})
                    logger.debug("Ping sent")
                except Exception:
                    break

    async def _recv_loop(self):
        async for msg_text in self.ws:
            try:
                packet = json.loads(msg_text)
                seq = packet.get("seq")
                payload = packet.get("payload", {})
                
                if seq in self._pending_requests:
                    self._pending_requests[seq].set_result(packet)
                    del self._pending_requests[seq]
                    continue
                
                # Парсинг входящих сообщений
                if "message" in payload and isinstance(payload["message"], dict):
                    msg = payload["message"]
                    chat_id = payload.get("chatId") or msg.get("chatId")
                    text = msg.get("text", "")
                    sender_id = msg.get("sender", 0)
                    msg_id = msg.get("id", "")
                    ts = msg.get("time", 0)
                    
                    if text and msg_id:
                        # Передаем в роутер команд
                        # Используем create_task чтобы не блокировать цикл чтения
                        asyncio.create_task(
                            self.dispatcher.process_message(self, msg_id, text, sender_id, ts)
                        )
            except json.JSONDecodeError:
                pass
            except Exception as e:
                logger.error(f"Error handling message: {e}")
