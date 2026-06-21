FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /home/user/app

COPY --chown=user:user requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY --chown=user:user . .

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/', timeout=5).read()"

CMD ["python", "main.py"]
