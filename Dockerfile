FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libffi-dev \
        libsodium-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -q -r requirements.txt

COPY cloud/ ./cloud/
COPY db.py dream.py digest.py sync.py migrate.py ./
COPY templates/ ./templates/
COPY static_dashboard/ ./static_dashboard/

RUN mkdir -p /app/data

ENV KONTEXT_DB_PATH=/app/data/kontext.db \
    KONTEXT_HOST=0.0.0.0 \
    KONTEXT_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${KONTEXT_PORT}/docs >/dev/null || exit 1

CMD ["python", "-m", "cloud.server"]
