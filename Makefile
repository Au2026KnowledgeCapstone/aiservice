.PHONY: init install browser migrate run test lint

init: install browser
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example; configure DATABASE_URL and SERVICE_TOKEN."; fi

install:
	python3.12 -m venv .venv
	.venv/bin/pip install -e '.[test,engine]'

browser:
	.venv/bin/python -m playwright install chromium

migrate:
	.venv/bin/alembic upgrade head

run:
	.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1

test:
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/python -m compileall -q app engines
