from fastapi import FastAPI, HTTPException, status

from app.schemas.jobs import JobEnvelope

app = FastAPI(title="BlindSpot AI Service", version="0.1.0")


@app.get("/health")
@app.get("/heath", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/jobs", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def submit_job(envelope: JobEnvelope) -> None:
    """Validate the envelope; durable job intake will be implemented next."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Job intake is not implemented yet.",
    )
