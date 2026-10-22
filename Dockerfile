FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install .
COPY migrations ./migrations
COPY alembic.ini ./

RUN useradd --create-home app && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]

FROM base AS test
USER root
RUN pip install ".[dev]"
COPY tests ./tests
COPY scripts ./scripts
USER app
CMD ["python", "-m", "pytest", "-q"]

# Cloud providers build the final stage unless a target is selected explicitly.
FROM base AS production
