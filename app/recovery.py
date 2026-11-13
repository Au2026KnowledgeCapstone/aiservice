"""Offline reconciliation after a process crash. Stop API workers before use.

Usage: .venv/bin/python -m app.recovery JOB_ID
This never reruns a browser or AI request and accesses only aiservice's DB.
"""
import argparse
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from app.callbacks.store import execution_event
from app.core.db import get_engine
from app.jobs.models import Job, now
from app.provider import finalize_costs


def recover_job(sessions, job_id):
    with sessions.begin() as db:
        job = db.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
        if job is None:
            raise ValueError("Job not found")
        changed = job.status in ("received", "running")
        if changed:
            job.status = "failed"
            job.error = "ProcessInterrupted"
            job.finished_at = now()
            execution_event(db, job_id, "job.failed", {"status": "failed", "error": "ProcessInterrupted"})
        if not job.costs_closed:
            finalize_costs(db, job_id)
        return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    args = parser.parse_args()
    try:
        changed = recover_job(sessionmaker(bind=get_engine()), args.job_id)
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    print("Interrupted job reconciled." if changed else "Terminal job already recorded; cost closure checked.")


if __name__ == "__main__":
    main()
