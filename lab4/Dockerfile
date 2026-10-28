# --- Стадия сборки: установка зависимостей ---
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt


# --- Стадия выполнения ---
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PORT=8000 \
    LOG_LEVEL=INFO

WORKDIR /app

COPY --from=builder /install /usr/local
COPY src ./src

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# exec-форма: uvicorn становится первым процессом контейнера (PID 1),
# поэтому к нему можно подключиться py-spy.
CMD ["uvicorn", "flightsearch.api_sync:app", "--host", "0.0.0.0", \
     "--port", "8000", "--workers", "1"]
