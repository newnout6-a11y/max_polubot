import sys
import os
import json
import asyncio

# Добавляем путь к локальной библиотеке PyMax из соседней папки
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "PyMax", "src")))

try:
    from pymax import Client, ConsoleSmsCodeProvider, ConsolePasswordProvider
except ImportError:
    print("Ошибка: не удалось импортировать библиотеку PyMax.")
    print("Убедитесь, что папка PyMax находится в одной директории с max_polubot.")
    sys.exit(1)

async def main():
    print("=== MAX Polubot: Локальная авторизация ===")
    phone = input("Введите ваш номер телефона (например, +79991234567): ").strip()
    if not phone:
        print("Номер телефона обязателен.")
        return
        
    print("\nИнициализация входа через SMS...")
    
    # Сохраняем временные файлы сессии во временную папку
    work_dir = os.path.abspath(".max_session")
    os.makedirs(work_dir, exist_ok=True)
    
    client = Client(
        phone=phone,
        work_dir=work_dir,
        session_name="session.db",
        sms_code_provider=ConsoleSmsCodeProvider(),
        password_provider=ConsolePasswordProvider(),
    )
    
    @client.on_start()
    async def _ready(c):
        print(f"\nУспешный вход в MAX!")
        
        session = c._app.session
        if not session or not session.token:
            print("Ошибка: не удалось получить токен сессии.")
            await c.stop()
            return
            
        session_data = {
            "deviceId": session.device_id,
            "token": session.token
        }
        
        # Сохраняем в файл session.json
        with open("session.json", "w") as f:
            json.dump(session_data, f, indent=2)
            
        print("\n==============================================")
        print("Файл session.json успешно создан в папке проекта!")
        print("Скопируйте его содержимое для секрета SESSION_JSON на Hugging Face:")
        print(json.dumps(session_data, indent=2))
        print("==============================================\n")
        
        await c.stop()

    try:
        await client.start()
    except Exception as e:
        print(f"Ошибка при авторизации: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nАвторизация отменена пользователем.")
