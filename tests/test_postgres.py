"""Opt-in PostgreSQL verification in a disposable schema, never BE's database.

AISERVICE_TEST_POSTGRES=1 .venv/bin/python -m pytest tests/test_postgres.py -q
Uses this service's configured DATABASE_URL and requires CREATE SCHEMA permission.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.callbacks.secrets import token_cipher
from app.callbacks.store import execution_event
from app.callbacks.worker import DeliveryWorker
from app.core.config import settings
from app.jobs.models import OutboxEntry
from app.jobs.store import accept_job
from app.schemas.jobs import JobEnvelope
from tests.test_auth import PAYLOAD
from tests.test_intake import config

pytestmark = pytest.mark.skipif(os.getenv("AISERVICE_TEST_POSTGRES") != "1", reason="Opt-in PostgreSQL test")


def test_postgres_migrations_concurrent_idempotency_and_sequence():
    url = settings().database_url
    assert url.startswith("postgresql")
    schema = "aiservice_test_" + uuid.uuid4().hex
    admin = create_engine(url)
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        alembic = Config("alembic.ini")
        with engine.connect() as connection:
            alembic.attributes["connection"] = connection
            command.upgrade(alembic, "head")
            command.check(alembic)
        sessions = sessionmaker(bind=engine)
        envelope = JobEnvelope.model_validate(PAYLOAD)
        cipher = token_cipher(config())
        with ThreadPoolExecutor(max_workers=6) as pool:
            accepted = list(pool.map(lambda _: accept_job(sessions, envelope, cipher), range(6)))
        assert accepted.count(True) == 1
        def event(_):
            with sessions.begin() as db:
                execution_event(db, envelope.job_id, "test", {})
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(event, range(6)))
        with sessions() as db:
            rows = db.scalars(select(OutboxEntry).order_by(OutboxEntry.sequence)).all()
            assert [r.sequence for r in rows] == list(range(1, 7))
        workers = [DeliveryWorker(config(), sessions) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(lambda w: w.claim(), workers))
        assert sum(entry is not None for entry in claimed) == 1
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
