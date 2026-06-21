import asyncio
import logging
import json
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
import uvicorn

from core.config import (
    TARGET_CHAT_ID, ADMIN_IDS, 
    REPORT_DAY_OF_WEEK, REPORT_HOUR, REPORT_MINUTE,
    QUEUE_MIN_DELAY, QUEUE_MAX_DELAY
)
from core.client import MaxWebsocketClient
from core.queue import MessageQueue
from core.dispatcher import Dispatcher
from db.models import Database
from ai.parser import parse_financial_message
from handlers.commands import cmd_ping, cmd_stata, cmd_help
from handlers.finance import handle_financial_message

# FastAPI app for Hugging Face health checks & keep-alive
app = FastAPI(title="MAX Polubot Keep-Alive")

@app.get("/")
async def health_check():
    return {
        "status": "ok", 
        "bot": "MAX Polubot is running",
        "database": "connected" if Database.pool else "disconnected"
    }

# Logging setup with rotation capability in production
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def background_ai_processor():
    """Фоновый воркер для парсинга финансов с Retry механикой."""
    while True:
        try:
            unparsed = await Database.get_unparsed_messages()
            for row in unparsed:
                msg_id = row['id']
                text = row['text']
                ts = row['timestamp']
                
                logger.info(f"AI Processing message {msg_id}...")
                try:
                    transactions = await parse_financial_message(text)
                    
                    for t in transactions:
                        await Database.save_finance(msg_id, t.category, t.expense, t.income, ts)
                        logger.info(f"Saved tx: {t.category} | Exp: {t.expense} | Inc: {t.income}")
                        
                    await Database.mark_parsed(msg_id)
                except Exception as e:
                    # Если API упало, оставляем is_parsed=FALSE и попробуем в следующем цикле
                    logger.error(f"Failed to parse {msg_id}, will retry later. Error: {e}")
                
                # Rate limiting for Gemini
                await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"Error in background AI loop: {e}")
            
        await asyncio.sleep(15)

async def cron_weekly_report(queue):
    """Еженедельный автоматический отчет."""
    from handlers.commands import cmd_stata
    # Dummy client adapter for the command handler
    class DummyClient:
        def __init__(self, q):
            self.queue = q
    await cmd_stata(DummyClient(queue), "", 0)

async def main():
    logger.info("Starting MAX Polubot (Production Mode)...")
    
    # 1. Init DB
    try:
        await Database.init()
    except Exception as e:
        logger.critical(f"Failed to initialize database: {e}")
        return
    
    # 2. Get credentials (either from environment variable SESSION_JSON or session.json file)
    device_id = None
    token = None
    
    # Check environment variable first (recommended for cloud deployments)
    session_env = os.getenv("SESSION_JSON")
    if session_env:
        try:
            data = json.loads(session_env)
            device_id = data.get("deviceId")
            token = data.get("token")
            logger.info("Credentials loaded successfully from SESSION_JSON environment variable.")
        except Exception as e:
            logger.error(f"Failed to parse SESSION_JSON environment variable: {e}")
            
    # Fallback to local file if env is not set
    if not device_id or not token:
        try:
            with open("session.json", "r") as f:
                data = json.load(f)
                device_id = data.get("deviceId")
                token = data.get("token")
                logger.info("Credentials loaded successfully from local session.json file.")
        except FileNotFoundError:
            pass
            
    if not device_id or not token:
        logger.critical("Authentication credentials not found! Please run auth.py locally or set the SESSION_JSON environment variable.")
        return
        
    # 3. Setup Dispatcher (Router)
    dispatcher = Dispatcher(admin_ids=ADMIN_IDS)
    dispatcher.register_command("пинг", cmd_ping)
    dispatcher.register_command("стата", cmd_stata)
    dispatcher.register_command("хелп", cmd_help)
    dispatcher.set_default_handler(handle_financial_message)
    
    # 4. Setup Client & Queue
    client = MaxWebsocketClient(device_id, token, dispatcher)
    
    # Bind the queue to the client's actual send method
    async def _send_wrapper(text):
        await client.send_message(TARGET_CHAT_ID, text)
        
    queue = MessageQueue(
        send_func=_send_wrapper,
        min_delay=QUEUE_MIN_DELAY,
        max_delay=QUEUE_MAX_DELAY
    )
    
    # Inject queue into client so handlers can use `client.queue.put`
    client.queue = queue
    
    # 5. Setup Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        cron_weekly_report,
        CronTrigger(day_of_week=REPORT_DAY_OF_WEEK, hour=REPORT_HOUR, minute=REPORT_MINUTE),
        args=[queue]
    )
    
    # 6. Start all background tasks
    queue.start()
    scheduler.start()
    
    # AI Processor
    ai_task = asyncio.create_task(background_ai_processor())
    
    # Start FastAPI server on port 7860 (Hugging Face health check port)
    config = uvicorn.Config(app, host="0.0.0.0", port=7860, log_level="warning")
    server = uvicorn.Server(config)
    web_task = asyncio.create_task(server.serve())
    logger.info("Keep-alive FastAPI server started on port 7860.")
    
    # 7. Start Main Client Loop (Blocks)
    try:
        await client.start()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down background tasks...")
        queue.stop()
        ai_task.cancel()
        web_task.cancel()
        if Database.pool:
            await Database.pool.close()
            logger.info("Database connection pool closed.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Graceful shutdown initiated.")
