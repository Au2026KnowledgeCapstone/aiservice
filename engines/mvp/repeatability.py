"""Versioned, comparable identities for observed controls. No DB-backed replay here.

Ported from BlindSpot/backend/app/services/browser/repeatability.py. Only the
parts with no database dependency are carried over: `control_identity` and
`digest` are pure functions needed starting in Step 3 (action_contracts.py,
form_entries.py). `saved_plan` and `compare_results` query Scenario/TestResult/
AnalysisRun rows that have no equivalent here — aiservice stores no product
schema — and are deliberately not ported.

`fingerprint` is the reference's `execution_contract`, adapted: aiservice has
no `BrowserConfiguration` revision to track (`EngineConfiguration` has no
revision at all — see the A4 handoff notes) and the engine never knows which
model/provider is behind the injected `AIProvider` (A0 deliberately hides
that). Per the A4 handoff: compute this and hand it back to the caller: do
not persist it anywhere — aiservice's operational store is job bookkeeping,
not product state, and there is no table for a plan cache to live in. Whether
BE wants this value for its own repeatability comparisons is an open
question this leaves for BE to raise, not something to answer by inventing
a table here.
"""
import hashlib
import json
from importlib.metadata import PackageNotFoundError, version

from engines.base import Target
from engines.mvp.contracts import EngineConfiguration
from engines.mvp.defaults import AI_VISION_ENABLED, MAX_ACTIONS

FINGERPRINT_VERSION = "aiservice-mvp-fingerprint-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def control_identity(control):
    # IDs are page-local. Only a server-extracted semantic identity can be replayed.
    if control.get("locator"):
        return {"locator": control["locator"], **({"scope": control["scope"]} if control.get("scope") else {})}
    return {key: control.get(key) for key in ("element", "name", "placeholder") if control.get(key)}


def fingerprint(requirement: dict, target: Target, configuration: EngineConfiguration) -> dict:
    """A plan's repeatability fingerprint. Compute only — see the module docstring."""
    from engines.mvp.assessment import INSTRUCTION as assessment_instruction
    from engines.mvp.planner import INSTRUCTION as planner_instruction

    try:
        engine_version = version("playwright")
    except PackageNotFoundError:
        engine_version = "unavailable"
    return {
        "version": FINGERPRINT_VERSION,
        "requirement_fingerprint": digest(requirement),
        "target_url": target.url,
        "configuration_hash": digest({"objective_checks": [dict(check.__dict__) for check in configuration.objective_checks]}),
        "vision_enabled": AI_VISION_ENABLED,
        "planner_hash": digest(planner_instruction),
        "assessment_hash": digest(assessment_instruction),
        "playwright_version": engine_version,
        "assessment_contract": "goal-assessment-v3",
        "readiness": "visible-state-300ms-v2",
        "viewport": [target.viewport_width, target.viewport_height],
        "max_actions": MAX_ACTIONS,
    }
