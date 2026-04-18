# syntax=docker/dockerfile:1.7
# Single image shared by the api and trader services.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Minimal system deps: curl is handy for container-side healthchecks;
# the rest of the stack builds cleanly on the slim image (psycopg[binary],
# numpy/pandas all ship wheels for python:3.12-slim linux/amd64).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so image layer caches on code-only changes.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install -e .

# Project code
COPY common ./common
COPY trader ./trader
COPY api ./api
COPY ui ./ui
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY config.example.yaml ./config.example.yaml
COPY docker/entrypoint-api.sh /usr/local/bin/entrypoint-api.sh
COPY docker/entrypoint-trader.sh /usr/local/bin/entrypoint-trader.sh
RUN chmod +x /usr/local/bin/entrypoint-api.sh /usr/local/bin/entrypoint-trader.sh

EXPOSE 8000

# Default command — overridden per-service in docker-compose.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
