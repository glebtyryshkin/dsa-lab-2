FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /usr/local /usr/local
COPY app.py .

EXPOSE 8000

CMD ["gunicorn", "-b", "0.0.0.0:8000", "app:app"]
