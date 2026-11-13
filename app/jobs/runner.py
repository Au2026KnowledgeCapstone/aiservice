"""Process-local scheduling; database work uses independent sessions off the event loop."""
import asyncio
import base64
import logging
import uuid
from dataclasses import asdict

from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.jobs.models import Job, OutboxEntry, now
from app.jobs.store import accept_job
from app.callbacks.secrets import token_cipher
from app.callbacks.store import execution_event
from app.provider import MeteredProvider, finalize_costs
from app.schemas.jobs import JobEnvelope, to_requirement_snapshot
from engines.base import Credentials, Target
from engines.registry import build_engine

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, config: Settings, sessions: sessionmaker, engine_factory=build_engine, provider_factory=MeteredProvider):
        self.config = config
        self.sessions = sessions
        self.engine_factory = engine_factory
        self.provider_factory = provider_factory
        self.semaphore = asyncio.Semaphore(config.max_concurrent_jobs)
        self.tasks: set[asyncio.Task] = set()
        self.admissions: set[asyncio.Task] = set()
        self.job_ids: dict[asyncio.Task, str] = {}
        self.stopping = False

    def schedule(self, envelope: JobEnvelope) -> None:
        task = asyncio.create_task(self.run(envelope), name=f"job:{envelope.job_id}")
        self.tasks.add(task)
        self.job_ids[task] = envelope.job_id
        task.add_done_callback(self._done)

    def _done(self, task):
        self.tasks.discard(task)
        self.job_ids.pop(task, None)
        if not task.cancelled() and task.exception() is not None:
            # Avoid exception messages: engines may include credentials in them.
            logger.error("Job task failed (%s)", type(task.exception()).__name__)

    async def admit(self, envelope):
        async def commit_and_schedule():
            created = await asyncio.to_thread(accept_job, self.sessions, envelope, token_cipher(self.config))
            if created:
                self.schedule(envelope)
            return created

        task = asyncio.create_task(commit_and_schedule())
        self.admissions.add(task)
        def done(completed):
            self.admissions.discard(completed)
            if not completed.cancelled():
                completed.exception()  # Observe errors even if the HTTP client disconnects.
        task.add_done_callback(done)
        # A client disconnect must not interrupt the commit-to-schedule handoff.
        return await asyncio.shield(task)

    async def shutdown(self):
        self.stopping = True
        if self.admissions:
            await asyncio.gather(*tuple(self.admissions), return_exceptions=True)
        tasks = tuple(self.tasks)
        job_ids = tuple(self.job_ids[task] for task in tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Also covers tasks cancelled before their coroutine's first instruction.
        for job_id in job_ids:
            await asyncio.to_thread(self._cancel_unfinished, job_id)

    def _cancel_unfinished(self, job_id):
        with self.sessions.begin() as db:
            job = db.get(Job, job_id)
            if job.status in ("received", "running"):
                job.status = "cancelled"
                job.finished_at = now()
                execution_event(db, job_id, "job.cancelled", {"status": "cancelled"})
                finalize_costs(db, job_id)

    async def db_call(self, function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Finish this transaction before the cancellation status is written.
            await task
            raise

    def _status(self, job_id, status, error=None):
        with self.sessions.begin() as db:
            job = db.get(Job, job_id)
            job.status = status
            job.error = error
            if status == "running":
                job.started_at = now()
            else:
                job.finished_at = now()
            execution_event(db, job_id, f"job.{status}", {"status": status, "error": error})
            if status in ("finished", "failed", "cancelled"):
                finalize_costs(db, job_id)

    async def status(self, job_id, status, error=None):
        await self.db_call(self._status, job_id, status, error)

    def _write_entry(self, job_id, kind, payload, entry_id):
        with self.sessions.begin() as db:
            db.add(OutboxEntry(id=entry_id, job_id=job_id, kind=kind, payload=payload))
            if kind == "requirement_outcome":
                execution_event(db, job_id, "requirement.completed", payload)

    async def entry(self, job_id, kind, payload):
        entry_id = str(uuid.uuid4())
        await self.db_call(self._write_entry, job_id, kind, payload, entry_id)
        return entry_id

    async def run(self, envelope):
        try:
            async with self.semaphore:
                await self.status(envelope.job_id, "running")
                async with asyncio.timeout(self.config.job_timeout_seconds):
                    await self.execute(envelope)
                await self.status(envelope.job_id, "finished")
        except asyncio.CancelledError:
            await self.status(envelope.job_id, "cancelled")
            raise
        except Exception as exc:
            # Store only a type, never arbitrary engine exception text.
            await self.status(envelope.job_id, "failed", type(exc).__name__)

    async def execute(self, envelope):
        engine = None
        session = None
        try:
            engine = self.engine_factory(
                envelope.engine, provider=self.provider_factory(self.sessions, envelope.job_id),
            )
            session = await engine.prepare(
                Target(envelope.target_url, envelope.viewport_width, envelope.viewport_height),
                Credentials(envelope.credentials),
            )
            requirements = tuple(to_requirement_snapshot(r) for r in envelope.requirements)
            await engine.survey(session, requirements)
            sink = OutboxEvidenceSink(self, envelope.job_id)
            for requirement in requirements:
                outcome = await engine.execute(session, requirement, sink)
                await self.entry(envelope.job_id, "requirement_outcome", {
                    "run_id": envelope.run_id,
                    "requirement_id": requirement.id,
                    "outcome": asdict(outcome),
                })
        finally:
            # Attempt both closes even when the first fails; bound each cleanup.
            try:
                if session is not None:
                    async with asyncio.timeout(5):
                        await session.close()
            finally:
                if engine is not None:
                    async with asyncio.timeout(5):
                        await engine.close()


class OutboxEvidenceSink:
    def __init__(self, runner, job_id):
        self.runner = runner
        self.job_id = job_id

    async def put_text(self, body, *, kind):
        return await self.runner.entry(self.job_id, "evidence", {"kind": kind, "text": body})

    async def put_screenshot(self, data, *, step_index=None):
        return await self.runner.entry(self.job_id, "evidence", {
            "kind": "screenshot", "step_index": step_index,
            "base64": base64.b64encode(data).decode("ascii"),
        })
