import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.core.auth import require_service_token
from app.core.config import settings
from app.callbacks.worker import DeliveryWorker
from app.jobs.runner import JobRunner
from app.jobs.store import JobConflict, job_status
from app.schemas.jobs import JobEnvelope
from engines.registry import build_engine
from app.provider import MeteredProvider
from app.capabilities.service import Capabilities
from app.capabilities.schemas import ExtractionRequest, ExtractionResponse, VerificationRequest, VerificationResponse, EvidenceResponse
from app.capabilities.errors import CapabilityError


def create_app(config=None, sessions=None, engine_factory=build_engine, callback_client=None, start_delivery=True, provider_factory=MeteredProvider, capabilities_factory=Capabilities) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        resolved = config or settings()
        db_engine = None
        factory = sessions
        if factory is None:
            db_engine = create_engine(resolved.database_url, pool_pre_ping=True)
            factory = sessionmaker(bind=db_engine, expire_on_commit=False)
        application.state.capabilities = capabilities_factory(resolved, factory)
        application.state.runner = JobRunner(resolved, factory, engine_factory, provider_factory)
        application.state.capabilities.semaphore = application.state.runner.semaphore
        application.state.delivery = DeliveryWorker(resolved, factory, callback_client)
        if start_delivery:
            await application.state.delivery.start()
        try:
            yield
        finally:
            await application.state.runner.shutdown()
            await application.state.delivery.shutdown()
            if db_engine is not None:
                await asyncio.to_thread(db_engine.dispose)

    application = FastAPI(title="BlindSpot AI Service", version="0.1.0", lifespan=lifespan)
    if config is not None:
        application.dependency_overrides[settings] = lambda: config

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default missing-field errors echo the entire input envelope.
        # Return useful field errors without reflecting credentials or job tokens.
        return JSONResponse(status_code=422, content={"detail": [
            {key: error[key] for key in ("type", "loc", "msg")}
            for error in exc.errors()
        ]})

    @application.exception_handler(CapabilityError)
    async def capability_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    @application.post("/v1/imports", response_model=ExtractionResponse, dependencies=[Depends(require_service_token)])
    async def extract_documents(body: ExtractionRequest, request: Request):
        return await request.app.state.capabilities.run("import", body)

    @application.post("/v1/environments/verify", response_model=VerificationResponse, dependencies=[Depends(require_service_token)])
    async def verify_login(body: VerificationRequest, request: Request):
        return await request.app.state.capabilities.run("verify", body)

    @application.get("/v1/jobs/{job_id}/evidence/{evidence_id}", response_model=EvidenceResponse, dependencies=[Depends(require_service_token)])
    async def retrieve_evidence(job_id: str, evidence_id: str, request: Request):
        from app.capabilities.evidence import retrieve
        return await asyncio.to_thread(retrieve, request.app.state.runner.sessions, job_id, evidence_id)

    @application.get("/health")
    @application.get("/heath", include_in_schema=False)
    async def health(resolved=Depends(settings)) -> dict[str, str]:
        return {"status": "ok", "engine": resolved.engine}

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
        current = await asyncio.to_thread(job_status, runner.sessions, envelope.job_id)
        return {"job_id": envelope.job_id, "run_id": envelope.run_id,
                "duplicate": not created, "status": current}

    @application.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(require_service_token)])
    async def cancel_job(job_id: str, request: Request) -> dict:
        runner = request.app.state.runner
        status = await runner.cancel(job_id)
        if status is None:
            raise HTTPException(404, "Job not found.")
        return {"job_id": job_id, "status": status}

    return application


app = create_app()
