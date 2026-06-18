FROM node:20-alpine AS asset-builder

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY build.mjs ./
COPY static/css/ static/css/
COPY static/js/ static/js/
COPY static/style.css static/
RUN node build.mjs

FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python3 -m compileall -q . || true

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
COPY --from=asset-builder /app/static/dist/ /app/static/dist/

RUN mkdir -p flask_session uploads static/uploads && \
    chmod 755 flask_session uploads static/uploads && \
    adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:5000/health || exit 1

USER appuser

ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--timeout", "120", "app:app"]
