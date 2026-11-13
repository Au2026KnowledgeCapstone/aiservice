"""Versioned, comparable identities for observed controls. No DB-backed replay here.

Ported from BlindSpot/backend/app/services/browser/repeatability.py. Only the
parts with no database dependency are carried over: `control_identity` and
`digest` are pure functions needed starting in Step 3 (action_contracts.py,
form_entries.py). `saved_plan` and `compare_results` query Scenario/TestResult/
AnalysisRun rows that have no equivalent here — aiservice stores no product
schema — and are deliberately not ported; see Step 6 for the fingerprint-only
(compute, never persist) treatment this leaves open.
"""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def control_identity(control):
    # IDs are page-local. Only a server-extracted semantic identity can be replayed.
    if control.get("locator"):
        return {"locator": control["locator"], **({"scope": control["scope"]} if control.get("scope") else {})}
    return {key: control.get(key) for key in ("element", "name", "placeholder") if control.get(key)}
