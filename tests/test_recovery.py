from sqlalchemy import select
from app.jobs.models import Job, OutboxEntry
from app.provider import UsageRecord
from app.recovery import recover_job
from tests.test_intake import database
from tests.test_provider import setup


def test_offline_recovery_does_not_rerun_and_closes_unknown_usage(database):
    provider = setup(database)
    provider.reserve("interrupted-call", "judge", 123)
    with database.begin() as db:
        db.get(Job, "job-1").status = "running"
    assert recover_job(database, "job-1")
    assert not recover_job(database, "job-1")
    with database() as db:
        job = db.get(Job, "job-1")
        assert job.status == "failed"
        assert job.error == "ProcessInterrupted"
        assert job.costs_closed
        usage = db.get(UsageRecord, "interrupted-call")
        assert usage.status == "unknown"
        assert usage.amount is None
        events = db.scalars(select(OutboxEntry).order_by(OutboxEntry.sequence)).all()
        assert [row.kind for row in events] == ["ai_usage", "execution_event", "ai_usage", "cost_finalization"]
        assert len({row.id for row in events}) == 4
