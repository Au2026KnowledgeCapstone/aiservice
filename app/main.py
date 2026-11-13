import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.core.auth import require_service_token
from app.core.config import settings
from app.jobs.runner import JobRunner
from app.jobs.store import JobConflict
from app.schemas.jobs import JobEnvelope
from engines.registry import build_engine


def create_app(config=None, sessions=None, engine_factory=build_engine) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        resolved = config or settings()
        db_engine = None
        factory = sessions
        if factory is None:
            db_engine = create_engine(resolved.database_url, pool_pre_ping=True)
            factory = sessionmaker(bind=db_engine, expire_on_commit=False)
        application.state.runner = JobRunner(resolved, factory, engine_factory)
        try:
            yield
        finally:
            await application.state.runner.shutdown()
            if db_engine is not None:
                await asyncio.to_thread(db_engine.dispose)

    application = FastAPI(title="BlindSpot AI Service", version="0.1.0", lifespan=lifespan)
    if config is not None:
        application.dependency_overrides[settings] = lambda: config

    @application.get("/health")
    @application.get("/heath", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post(
        "/v1/jobs", status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_service_token)],
    )
    async def submit_job(envelope: JobEnvelope, request: Request) -> dict:
        runner = request.app.state.runner
        if runner.stopping:
            raise HTTPException(503, "Service is shutting down.")
        # Resolve defaults before persistence so retries compare effective engines.
        envelope = envelope.model_copy(update={"engine": envelope.engine or runner.config.engine})
        try:
            created = await runner.admit(envelope)
        except JobConflict:
            raise HTTPException(409, "Job ID already exists with a different envelope.") from None
        except SQLAlchemyError:
            raise HTTPException(503, "Job storage is unavailable.") from None
        return {"job_id": envelope.job_id, "run_id": envelope.run_id, "duplicate": not created}

    return application


app = create_app()
