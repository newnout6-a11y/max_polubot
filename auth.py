import asyncio
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOCAL_VENV_PYTHON = ROOT / "venv" / "Scripts" / "python.exe"

if (
    LOCAL_VENV_PYTHON.exists()
    and Path(sys.executable).resolve() != LOCAL_VENV_PYTHON.resolve()
    and os.getenv("MAX_AUTH_NO_VENV_REEXEC") != "1"
):
    os.execv(
        str(LOCAL_VENV_PYTHON),
        [str(LOCAL_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
    )

pymax_src = Path(os.getenv("PYMAX_SRC_PATH", ROOT.parent / "PyMax" / "src")).resolve()
sys.path.insert(0, str(pymax_src))

try:
    from pymax import ConsoleQrHandler, WebClient
except ImportError as exc:
    print("ERROR: failed to import PyMax.")
    print(f"PyMax path: {pymax_src}")
    print(f"Python: {sys.executable}")
    print(f"Reason: {exc}")
    print("")
    print("Try one of these commands:")
    print(r"  .\venv\Scripts\python.exe auth.py")
    print("  py -m pip install -r requirements.txt")
    sys.exit(1)

if os.getenv("MAX_AUTH_IMPORT_CHECK") == "1":
    print("AUTH_IMPORT_OK")
    print(f"Python: {sys.executable}")
    print(f"PyMax path: {pymax_src}")
    sys.exit(0)


async def main():
    print("=== MAX Polubot: local WEB authorization ===")
    print("The bot uses the MAX web websocket, so it needs a QR web session.")
    print("Open the QR in MAX and confirm login.")

    work_dir = Path(os.getenv("MAX_SESSION_DIR", ROOT / ".max_session")).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    session_db = work_dir / "session.db"
    if session_db.exists():
        session_db.unlink()

    client = WebClient(
        work_dir=str(work_dir),
        session_name="session.db",
        qr_provider=ConsoleQrHandler(),
    )

    @client.on_start()
    async def _ready(c):
        print("\nSuccessful WEB login to MAX!")

        session = c._app.session
        if not session or not session.token:
            print("ERROR: failed to get session token.")
            await c.stop()
            return

        session_data = {
            "deviceId": session.device_id,
            "token": session.token,
        }

        session_file = Path(os.getenv("SESSION_FILE", ROOT / "session.json")).resolve()
        with session_file.open("w", encoding="utf-8") as file:
            json.dump(session_data, file, indent=2, ensure_ascii=False)

        print("\n==============================================")
        print(f"File {session_file} was created.")
        print("Copy the JSON below into the Hugging Face SESSION_JSON secret:")
        print(json.dumps(session_data, indent=2, ensure_ascii=False))
        print("==============================================\n")

        await c.stop()

    try:
        await client.start()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print(f"Authorization error: {exc}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAuthorization cancelled by user.")
