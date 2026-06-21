FROM python:3.11-slim

# Создаем пользователя с UID 1000 (стандартный для Hugging Face Spaces)
RUN useradd -m -u 1000 user

# Устанавливаем рабочую директорию
WORKDIR /home/user/app

# Копируем файл зависимостей с правами пользователя
COPY --chown=user:user requirements.txt .

# Устанавливаем зависимости в локальную директорию пользователя
RUN pip install --no-cache-dir --user -r requirements.txt

# Копируем остальной код проекта с правами пользователя
COPY --chown=user:user . .

# Добавляем путь к локальным бинарникам пользователя в PATH
ENV PATH="/home/user/.local/bin:${PATH}"

# Открываем порт 7860 (Hugging Face Health Check)
EXPOSE 7860

# Запускаем бота
CMD ["python", "main.py"]
