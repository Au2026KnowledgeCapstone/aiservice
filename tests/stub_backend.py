"""Contract-test backend only; never presented as the real BE implementation."""
import asyncio
import hashlib
import json
from fastapi import FastAPI, HTTPException, Request
from app.callbacks.schemas import ExecutionEvent, UsageEvent, CostFinalization


class StubBackend:
    def __init__(self):
        self.app = FastAPI()
        self.events = {}
        self.usage = {}
        self.finalizations = {}
        self.finalized = asyncio.Event()
        self.unavailable = 0
        self.deliveries = 0
        self.applied_events = 0
        self.tokens = {"run-1": "callback-token"}

        async def authenticate(run_id, request):
            self.deliveries += 1
            if request.headers.get("Authorization") != "Bearer " + self.tokens.get(run_id, "invalid"):
                raise HTTPException(401)
            if self.unavailable:
                self.unavailable -= 1
                raise HTTPException(503)

        @self.app.post("/internal/v1/runs/{run_id}/execution-events")
        async def execution(run_id: str, body: ExecutionEvent, request: Request):
            await authenticate(run_id, request)
            if body.event_id not in self.events:
                self.events[body.event_id] = body.model_dump(mode="json")
                self.applied_events += 1
            return {"acknowledged": body.event_id}

        @self.app.post("/internal/v1/runs/{run_id}/ai-usage")
        async def usage(run_id: str, body: UsageEvent, request: Request):
            await authenticate(run_id, request)
            previous = self.usage.get(body.usage_id)
            if previous is None or previous.revision < body.revision:
                self.usage[body.usage_id] = body
            return {"acknowledged": body.event_id}

        @self.app.post("/internal/v1/runs/{run_id}/ai-cost-finalizations")
        async def finalize(run_id: str, body: CostFinalization, request: Request):
            await authenticate(run_id, request)
            records = sorted(self.usage.values(), key=lambda r: r.usage_id)
            from decimal import Decimal
            # Match the fixed-scale database representation used by the sender.
            items = [{"usage_id": r.usage_id, "revision": r.revision, "status": r.status,
                      "tokens": r.tokens.model_dump(),
                      "amount": str(r.amount.quantize(Decimal("0.000000000001"))) if r.amount is not None else None,
                      "currency": r.currency} for r in records]
            digest = hashlib.sha256(json.dumps(items, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if len(records) != body.final_usage_count or digest != body.final_usage_digest:
                raise HTTPException(409, "Missing final usage")
            self.finalizations.setdefault(body.event_id, body)
            self.finalized.set()
            return {"acknowledged": body.event_id}
