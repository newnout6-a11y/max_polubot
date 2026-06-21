FROM python:3.11-slim

RUN useradd -m -u 1000 user

WORKDIR /home/user/app

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user:user . .

ENV PATH="/home/user/.local/bin:${PATH}"

EXPOSE 7860

CMD ["python", "main.py"]
