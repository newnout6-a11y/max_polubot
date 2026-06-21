import time
import logging
from db.models import Database

logger = logging.getLogger(__name__)

async def cmd_ping(client, args, sender_id):
    """Проверка доступности бота"""
    await client.queue.put("🏓 Понг! Бот на связи и работает штатно.")

async def cmd_stata(client, args, sender_id):
    """Вывод статистики. Использование: !стата [месяц]"""
    period = 7 # days by default
    title = "неделю"
    
    if args and args.lower() == "месяц":
        period = 30
        title = "месяц"
        
    start_ts = int(time.time()) - (period * 24 * 60 * 60)
    
    stats, total_exp, total_inc = await Database.get_stats(start_ts)
    
    if not stats and total_exp == 0 and total_inc == 0:
        await client.queue.put(f"ℹ️ За {title} нет ни одной записи.")
        return
        
    report = f"📊 **Финансовая статистика за {title}**\n\n"
    for row in stats:
        cat = row['category'].capitalize()
        exp = row['total_expense']
        inc = row['total_income']
        
        line = f"• {cat}:"
        if exp > 0:
            line += f" 🔻{exp}"
        if inc > 0:
            line += f" 🍏{inc}"
        report += line + "\n"
        
    report += "\n"
    report += f"📉 Расход: {total_exp}\n"
    report += f"📈 Доход: {total_inc}\n"
    report += f"⚖️ Дельта: {total_inc - total_exp}\n"
    
    # Отправляем в очередь с rate limiting
    await client.queue.put(report)

async def cmd_help(client, args, sender_id):
    """Список команд"""
    text = (
        "🤖 **Команды MAX Polubot:**\n"
        "• `!стата` — сводка за 7 дней\n"
        "• `!стата месяц` — сводка за 30 дней\n"
        "• `!пинг` — проверка статуса\n"
        "• `!хелп` — это меню"
    )
    await client.queue.put(text)
