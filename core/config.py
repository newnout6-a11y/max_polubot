import os
from dotenv import load_dotenv

load_dotenv()

TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", "0"))
ADMIN_IDS = os.getenv("ADMIN_IDS", "").split(",")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

REPORT_DAY_OF_WEEK = os.getenv("REPORT_DAY_OF_WEEK", "fri")
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "18"))
REPORT_MINUTE = int(os.getenv("REPORT_MINUTE", "0"))

QUEUE_MIN_DELAY = float(os.getenv("QUEUE_MIN_DELAY", "3.0"))
QUEUE_MAX_DELAY = float(os.getenv("QUEUE_MAX_DELAY", "7.0"))
