import asyncio
import json
import os
import sys
import warnings
from pathlib import Path

import websockets
from dotenv import load_dotenv

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
    SESSION_FILE,
)


def load_session(root: Path):
    session_env = os.getenv("SESSION_JSON")
    if session_env:
        return "SESSION_JSON", json.loads(session_env)

    path = root / SESSION_FILE
    return SESSION_FILE, json.loads(path.read_text(encoding="utf-8"))


def packet(seq, opcode, payload):
    return {
        "seq": seq,
        "opcode": opcode,
        "payload": payload,
        "ver": MAX_PROTOCOL_VERSION,
        "cmd": 0,
    }


async def send_recv(ws, seq, opcode, payload):
    await ws.send(json.dumps(packet(seq, opcode, payload), ensure_ascii=False))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=20)
        message = json.loads(raw)
        if message.get("seq") == seq:
            return message


def hello_payload(device_id):
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


def iter_chats(payload):
    candidates = [
        payload.get("chats"),
        payload.get("dialogs"),
        payload.get("conversationList"),
    ]

    for candidate in candidates:
        if isinstance(candidate, list):
            yield from candidate

    sync = payload.get("sync") or payload.get("syncState") or {}
    if isinstance(sync, dict):
        for key in ("chats", "dialogs", "conversationList"):
            value = sync.get(key)
            if isinstance(value, list):
                yield from value


def iter_users(payload):
    candidates = [
        payload.get("users"),
        payload.get("contacts"),
    ]

    profile = payload.get("profile") or payload.get("me") or payload.get("user")
    if isinstance(profile, dict):
        yield profile
        contact = profile.get("contact")
        if isinstance(contact, dict):
            yield contact

    for candidate in candidates:
        if isinstance(candidate, list):
            yield from candidate

    sync = payload.get("sync") or payload.get("syncState") or {}
    if isinstance(sync, dict):
        for key in ("users", "contacts"):
            value = sync.get(key)
            if isinstance(value, list):
                yield from value


def chat_field(chat, *names):
    for name in names:
        if isinstance(chat, dict) and chat.get(name) is not None:
            return chat[name]
    return None


def user_field(user, *names):
    for name in names:
        if isinstance(user, dict) and user.get(name) is not None:
            return user[name]
    return None


async def main():
    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")

    source, data = load_session(root)
    device_id = data.get("deviceId") or data.get("device_id")
    token = data.get("token")
    if not device_id or not token:
        print("ERROR: session must contain deviceId/device_id and token")
        return

    print(f"Session source: {source}")
    print("Mode: read-only raw websocket; no auth flow, no .max_session, no chat messages")

    async with websockets.connect(
        MAX_WS_URL,
        origin=MAX_WS_ORIGIN,
        user_agent_header=MAX_USER_AGENT,
        ping_interval=None,
    ) as ws:
        await send_recv(ws, 1, 6, hello_payload(device_id))
        login = await send_recv(
            ws,
            2,
            19,
            {
                "token": token,
                "interactive": True,
                "chatsCount": 100,
                "chatsSync": 0,
                "contactsSync": 0,
                "presenceSync": 0,
                "draftsSync": 0,
            },
        )

    payload = login.get("payload") or {}
    if "error" in payload:
        print("AUTH_RESULT=FAIL")
        print(f"AUTH_ERROR={payload.get('error')}")
        print(f"AUTH_MESSAGE={payload.get('message') or payload.get('localizedMessage') or ''}")
        return

    chats = list(iter_chats(payload))
    if not chats:
        print("No chats found in login payload.")
        print("Payload keys:", ", ".join(sorted(payload.keys())))
    else:
        print("=== CHAT_LIST_START ===")
        for chat in chats:
            chat_id = chat_field(chat, "id", "chatId", "conversationId")
            title = chat_field(chat, "title", "name", "chatTitle") or "Untitled"
            chat_type = chat_field(chat, "type", "chatType") or "unknown"
            print(f"CHAT_NAME: {title} | CHAT_ID: {chat_id} | TYPE: {chat_type}")
        print("=== CHAT_LIST_END ===")

    seen_user_ids = set()
    print("=== USER_LIST_START ===")
    for user in iter_users(payload):
        user_id = user_field(user, "id", "userId", "contactId")
        if user_id in seen_user_ids:
            continue
        seen_user_ids.add(user_id)
        name = (
            user_field(user, "name", "title", "firstName", "displayName", "fullName")
            or "Untitled"
        )
        username = user_field(user, "username", "login")
        print(f"USER_NAME: {name} | USER_ID: {user_id} | USERNAME: {username or ''}")
    print("=== USER_LIST_END ===")


if __name__ == "__main__":
    if sys.platform == "win32":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
