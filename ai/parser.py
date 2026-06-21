import json
import logging
from pydantic import BaseModel, Field
from google import genai
from typing import List
from core.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

# Configured client
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    logger.error(f"Failed to init Gemini client: {e}")
    client = None

class Transaction(BaseModel):
    category: str = Field(description="The category or item name (e.g. 'сольцы', 'бензин', 'продукты'). Should be lowercase.")
    expense: int = Field(default=0, description="The amount spent. Should be a positive integer.")
    income: int = Field(default=0, description="The amount earned. Should be a positive integer.")

class ExtractionResult(BaseModel):
    transactions: List[Transaction] = Field(description="List of extracted transactions.")

async def parse_financial_message(text: str) -> List[Transaction]:
    """
    Парсит сообщение через AI с защитой от сбоев API.
    Raises Exception если API отвалилось (для повторной попытки).
    """
    if not client:
        raise RuntimeError("Gemini API Client is not initialized.")
        
    prompt = f"""
    You are a financial assistant reading messages from a team chat.
    Extract any financial transactions (expenses and incomes) from the message.
    For example: 'сольцы -200/140' means expense is 200, income is 140, category is 'сольцы'.
    If a number has a minus sign or is described as spent, it is an expense.
    If a number is described as received, it is an income.
    If there are no financial transactions, return an empty list.
    
    Message: "{text}"
    """
    
    try:
        # Run synchronous call in thread pool to not block asyncio
        import asyncio
        loop = asyncio.get_running_loop()
        
        def run_sync():
            return client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config={
                    'response_mime_type': 'application/json',
                    'response_schema': ExtractionResult,
                },
            )
            
        response = await loop.run_in_executor(None, run_sync)
        
        data = json.loads(response.text)
        result = ExtractionResult(**data)
        return result.transactions
        
    except Exception as e:
        logger.error(f"AI API Error: {e}")
        # Пробрасываем ошибку выше, чтобы не помечать сообщение как parsed
        raise
