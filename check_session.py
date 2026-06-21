import asyncio
import json
import os
import sys
import warnings
from pathlib import Path

from dotenv import load_dotenv

from core.config import SESSION_FILE
from core.session_probe import probe_session


def load_session(root: Path):
    session_env = os.getenv("SESSION_JSON")
    if session_env:
        return "SESSION_JSON", json.loads(session_env)

    path = root / SESSION_FILE
    return SESSION_FILE, json.loads(path.read_text(encoding="utf-8"))


async def main():
    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")

    source, data = load_session(root)
    device_id = data.get("deviceId") or data.get("device_id")
    token = data.get("token")

    print(f"source={source}")
    print(f"device_id_present={bool(device_id)}")
    print(f"token_len={len(token or '')}")

    result = await probe_session(device_id, token)
    if result.ok:
        print("AUTH_RESULT=OK")
        return

    print("AUTH_RESULT=FAIL")
    print(f"AUTH_ERROR={result.error}")
    print(f"AUTH_MESSAGE={result.message or ''}")


if __name__ == "__main__":
    if sys.platform == "win32":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
