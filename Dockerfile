# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# base: production image (app code + runtime dependencies only).
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Non-root user (least privilege; the app has no writable state anyway).
RUN useradd --create-home --uid 10001 appuser

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

# Migrations are a separate concern: run them with the `migrate` compose
# service (or `alembic upgrade head`) before starting app replicas.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]


# ---------------------------------------------------------------------------
# dev: base + test tooling, used by the `test` service in docker-compose.
# ---------------------------------------------------------------------------
FROM base AS dev

USER root
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY pyproject.toml ./
COPY tests ./tests

USER appuser

# The image filesystem is read-only for the runtime user, so pytest's cache
# provider is disabled to avoid permission warnings.
CMD ["pytest", "-q", "-p", "no:cacheprovider"]
