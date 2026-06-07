FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

COPY pyproject.toml README.md server.py run.py .env.example ./
COPY src ./src
COPY frontend ./frontend
COPY data ./data
COPY telegram_bot ./telegram_bot

RUN mkdir -p output && chown -R appuser:appuser /app

USER appuser

EXPOSE 9090

CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "9090"]