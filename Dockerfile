FROM python:3.12-slim
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY engines ./engines
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir '.[engine]' \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 aiservice \
    && chown -R aiservice:aiservice /app "$PLAYWRIGHT_BROWSERS_PATH"

USER aiservice
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
