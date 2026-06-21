import asyncio
import json
from dataclasses import dataclass

import websockets

from core.config import (
    MAX_APP_VERSION,
    MAX_DEVICE_LOCALE,
    MAX_DEVICE_NAME,
    MAX_DEVICE_TYPE,
    MAX_LOCALE,
    MAX_OS_VERSION,
    MAX_PROTOCOL_VERSION,
    MAX_SCREEN,
    MAX_TIMEZONE,
    MAX_USER_AGENT,
    MAX_WS_ORIGIN,
    MAX_WS_URL,
    SESSION_CHECK_TIMEOUT_SECONDS,
)


@dataclass(frozen=True)
class SessionProbeResult:
    ok: bool
    error: str | None = None
    message: str | None = None

    @property
    def invalid_session(self) -> bool:
        return self.error in {"login.token", "login.auth", "login.session"}


def _packet(seq, opcode, payload):
    return {
        "seq": seq,
        "opcode": opcode,
        "payload": payload,
        "ver": MAX_PROTOCOL_VERSION,
        "cmd": 0,
    }


async def _send_recv(ws, seq, opcode, payload):
    await ws.send(json.dumps(_packet(seq, opcode, payload), ensure_ascii=False))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=SESSION_CHECK_TIMEOUT_SECONDS)
        message = json.loads(raw)
        if message.get("seq") == seq:
            return message


def _hello_payload(device_id):
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
        "deviceId": device_id,
    }


async def probe_session(device_id: str | None, token: str | None) -> SessionProbeResult:
    if not device_id or not token:
        return SessionProbeResult(
            ok=False,
            error="session.missing",
            message="deviceId/device_id and token are required",
        )

    try:
        async with websockets.connect(
            MAX_WS_URL,
            origin=MAX_WS_ORIGIN,
            user_agent_header=MAX_USER_AGENT,
            ping_interval=None,
            open_timeout=SESSION_CHECK_TIMEOUT_SECONDS,
            close_timeout=5,
        ) as ws:
            await _send_recv(ws, 1, 6, _hello_payload(device_id))
            login = await _send_recv(
                ws,
                2,
                19,
                {
                    "token": token,
                    "interactive": True,
                    "chatsCount": 1,
                    "chatsSync": 0,
                    "contactsSync": 0,
                    "presenceSync": 0,
                    "draftsSync": 0,
                },
            )
    except Exception as exc:
        return SessionProbeResult(ok=False, error="probe.error", message=str(exc))

    payload = login.get("payload") or {}
    if "error" in payload:
        return SessionProbeResult(
            ok=False,
            error=payload.get("error"),
            message=payload.get("message") or payload.get("localizedMessage") or "",
        )

    return SessionProbeResult(ok=True)
