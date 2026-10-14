# --- Стадия сборки: установка зависимостей ---
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt


# --- Стадия выполнения ---
FROM python:3.11-slim AS runtime

# один процесс gunicorn: поездки хранятся в памяти
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    LOG_LEVEL=INFO \
    PAYMENT_APPROVE_AUTH=true \
    PAYMENT_CAPTURE_OK=true \
    PARKING_ALLOWED=true \
    PARKING_ZONE="Зона А"

WORKDIR /app

COPY --from=builder /install /usr/local

COPY app.py ./
COPY src ./src
COPY web ./web

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
