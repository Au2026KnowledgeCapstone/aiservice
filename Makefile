.PHONY: install test lint

install:
	python3.12 -m venv .venv
	.venv/bin/pip install -e '.[test]'

test:
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/python -m compileall -q app engines
