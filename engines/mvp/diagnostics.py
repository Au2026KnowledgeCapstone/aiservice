"""Server-owned failure locations and references; never fabricated evidence.

Ported from BlindSpot/backend/app/services/browser/diagnostics.py.
`startup_failure` writes its screenshot through the EvidenceSink instead of a
storage path; `screenshot_key` on the resulting observation holds the evidence
id the sink returned.
"""
import time
from urllib.parse import urlsplit

from engines.mvp.errors import EngineError, PolicyDenied
from engines.mvp.execution import Execution
from engines.mvp.page_summary import describe_page
from engines.mvp.policy import origin


def safe_endpoint(url, redactor):
    try:
        return redactor.text(origin(url) + (urlsplit(url).path or "/"), 1200)
    except ValueError:
        return "[unavailable URL]"


UNREACHABLE_REPORT_LIMIT = 8


def unreachable_controls(observations, redactor):
    """Visible controls the engine observed but could not use, and why.

    `inspect()` already keeps a control it cannot identify: it records the
    control, computes no locator, marks it `actionable=False` and moves on.
    Nothing ever reported that, which is what turned "the cart was never tested"
    into an inconclusive result with no stated cause -- the fact was sitting in
    the observation the whole time.

    Only reasons that describe the page are collected. A disabled control is
    usually deliberate, and a redundant copy of an interchangeable control was
    withdrawn on purpose, so neither is a finding.
    """
    found = {}
    for observation in observations:
        for control in observation.get("controls", []):
            if control.get("actionable") or control.get("disabled") or control.get("redundant_copy"):
                continue
            name = (control.get("name") or "").strip()
            if not control.get("locator") and not name:
                reason = "no accessible name"
            elif control.get("ambiguous"):
                reason = "several controls share one identity"
            else:
                continue
            endpoint = safe_endpoint(control["href"], redactor) if control.get("href") else None
            key = (reason, control.get("role"), endpoint or name.casefold())
            entry = found.setdefault(key, {
                "reason": reason, "role": control.get("role"), "element": control.get("element"),
                "name": redactor.text(name, 120) if name else None, "endpoint": endpoint, "occurrences": 0,
            })
            entry["occurrences"] += 1
    ordered = sorted(found.values(), key=lambda item: (item["reason"], item["endpoint"] or item["name"] or ""))
    return ordered[:UNREACHABLE_REPORT_LIMIT]


def unreachable_summary(items):
    """One sentence for the user-facing result; the detail stays in the log.

    Scoped to the unnamed case on purpose: that one is both the reason the
    engine could not act and a defect in the page itself, so it is worth a
    sentence someone can act on. Ambiguity is recorded but not narrated, because
    it describes a layout rather than something to go and fix.
    """
    unnamed = [item for item in items if item["reason"] == "no accessible name"]
    if not unnamed:
        return None
    # A destination identifies the control to someone reading this; a bare tag
    # name does not, so named-by-URL ones lead and the rest fall into "and more".
    unnamed = sorted(unnamed, key=lambda item: (item["endpoint"] is None, item["endpoint"] or ""))
    shown = [item["endpoint"] or f"an unnamed <{item['element'] or 'control'}>" for item in unnamed[:3]]
    more = f", and {len(unnamed) - 3} more" if len(unnamed) > 3 else ""
    subject = "control has" if len(unnamed) == 1 else "controls have"
    return (
        f"{len(unnamed)} visible {subject} no accessible name and could not be tested: "
        f"{', '.join(shown)}{more}."
    )


def explain_error(exc, stage):
    stage = stage.replace("_", " ")
    if isinstance(exc, EngineError):
        return str(exc)
    if isinstance(exc, PolicyDenied):
        return "Execution policy blocked this operation; this is not evidence of a product defect."
    if isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutError":
        return f"Timed out during {stage}. Completion could not be verified; the action was not replayed."
    return f"{stage.capitalize()} did not complete reliably. The underlying application cause has not been established."


def short_failure_reason(point):
    """Summarize only recorded facts; keep full diagnostics in the stored point."""
    stage = point["stage"]
    detail = point["reason"]
    lower = detail.lower()
    step = point.get("step_index")
    prefix = f"Step {step + 1}: " if step is not None and step >= 0 else ""
    requests = point.get("network_errors") or []
    denials = [item for item in point.get("policy_denials", []) if item.get("critical")]
    if stage == "state_transition" and "page stayed blank" in lower:
        message = "The destination page stayed blank; navigation could not be verified."
    elif denials:
        message = "A required connection was blocked."
    elif requests:
        request = requests[0]
        path = urlsplit(request.get("endpoint", "")).path or "/"
        path = path if len(path) <= 70 else path[:67] + "…"
        message = f"{path} returned HTTP {request['status']}." if request.get("status") else f"{path} could not be reached."
    elif point.get("navigation", {}).get("status") == "arrived" and stage in {"browser_action", "state_transition", "page_readiness", "observation_capture"}:
        message = "The destination was reached, but its screenshot could not be verified." if stage == "observation_capture" else "The destination was reached, but the page was not ready for verification."
    elif stage in {"contract_validation", "action_verification"}:
        message = detail.split("\n", 1)[0][:180]
    elif stage == "element_selection":
        if "changed" in lower:
            message = "The selected control changed before the action."
        elif "saved control" in lower:
            message = "The saved control is missing or no longer unique."
        elif "multiple controls" in lower:
            message = "Multiple controls match the requested target."
        elif "no controls" in lower:
            message = "No usable controls were found on the page."
        else:
            message = "The intended control could not be identified reliably."
    elif stage == "ai_element_selection":
        message = "AI could not select a page control."
    elif stage == "state_transition":
        message = "The planned destination URL was not reached." if point.get("navigation") else "The expected page data change could not be verified."
    elif stage == "observation_capture":
        message = "The screenshot did not show the expected data change." if "new visual state" in lower else "The page changed while the screenshot was being taken."
    elif stage == "page_readiness":
        message = "The page did not finish loading."
    elif stage == "opening_target":
        message = "The target page could not be opened."
    elif "timed out" in lower:
        message = f"The {point.get('action') or 'browser'} action timed out."
    elif stage == "requirement_assessment":
        import re
        message = re.split(r"(?<=[.!?])\s+", detail.strip(), maxsplit=1)[0]
        if len(message) > 180:
            message = message[:177].rsplit(" ", 1)[0] + "…"
        if not message:
            message = "There was not enough evidence to verify this criterion."
    elif "policy" in lower or "blocked" in lower:
        message = "Execution policy blocked this action."
    elif stage == "network_validation":
        message = "A required network request failed."
    else:
        message = "The browser action could not be completed."
    return prefix + message


def failure_point(*, stage, reason, expected, step=None, observations=(), event=None, criterion_index=None, status="inconclusive", offset=None):
    refs = [item["observation_id"] for item in observations]
    screenshots = [item["screenshot_key"] for item in observations if item.get("screenshot_captured") and item.get("screenshot_key")]
    times = [item["video_offset_seconds"] for item in observations if item.get("video_offset_seconds") is not None]
    location = offset if offset is not None else (max(times) if times else None)
    return {
        "type": "failure_point", "stage": stage, "step_index": step,
        "action": event.get("action") if event else None, "intent": event.get("intent") if event else None,
        "criterion_index": criterion_index, "status": status, "expected_behavior": expected, "reason": reason,
        "observation_ids": refs, "screenshot_keys": screenshots,
        "actual_behavior": describe_page(observations[-1].get("accessible_ui", "") if observations else "", expected, observations[-1].get("url", "") if observations else ""),
        "actual_behavior_format": "sentence-v2", "observed_url": observations[-1].get("url", "") if observations else "",
        "raw_page_state": observations[-1].get("accessible_ui", "")[:3000] if observations else "",
        "screenshot_error": (
            "Some checkpoints have no screenshot: " + ", ".join(item["observation_id"] for item in observations if not item.get("screenshot_captured")) + ". Available images may show supporting context only."
        ) if any(not item.get("screenshot_captured") for item in observations) else None,
        "video_offset_seconds": location,
        "video_start_seconds": max(0, min(times + [location]) - 3) if location is not None else None,
        "video_end_seconds": location + 3 if location is not None else None,
        "video_timing": "approximate" if location is not None else "unavailable",
    }


def assessment_failures(assessment, requirement, observations, actions):
    points = []
    for criterion in assessment.criteria:
        if criterion.status == "satisfied":
            continue
        cited = [item for item in observations if item["observation_id"] in criterion.evidence_ids]
        matching = [action for action in actions if action.get("before_observation_id") in criterion.evidence_ids and action.get("after_observation_id") in criterion.evidence_ids]
        event = matching[0] if len(matching) == 1 else None
        context_only = not cited
        if context_only and observations:
            cited = [observations[-1]]
        point = failure_point(
            stage="requirement_assessment", reason=criterion.explanation,
            expected=requirement["acceptance_criteria"][criterion.criterion_index],
            step=event.get("step_index") if event else None, event=event, observations=cited,
            criterion_index=criterion.criterion_index, status=criterion.status,
        )
        point["evidence_role"] = "context_only" if context_only else "cited_observations"
        point["short_reason"] = short_failure_reason(point)
        points.append(point)
    return points


async def startup_failure(session, page, exc, plan, sink):
    """Preserve evidence when the recorded target cannot open before execution."""
    result = Execution(classification="environment_failure")
    reason = session.redactor.text(explain_error(exc, "opening the target page"), 2000)
    start = getattr(session, "recording_started_at", None)
    offset = round(max(0, time.monotonic() - start), 3) if start is not None else None
    observation = {
        "observation_id": "startup-failure", "step_index": -1, "capture_kind": "failure",
        "capture_goal": session.redactor.text(plan.objective, 500), "url": safe_endpoint(page.url, session.redactor),
        "accessible_ui": "", "video_offset_seconds": offset, "screenshot_captured": False,
    }
    try:
        png = await page.screenshot(full_page=True, mask=[page.locator('input[type="password"],input[type="email"]')], timeout=5000)
        evidence_id = await sink.put_screenshot(png, step_index=-1)
        observation.update(screenshot_captured=True, screenshot_key=evidence_id)
        result.media.append({"evidence_type": "screenshot", "evidence_id": evidence_id, "step_index": -1})
    except Exception:
        observation["screenshot_error"] = "The target/browser was unavailable; no screenshot could be saved."
    point = failure_point(stage="opening_target", reason=reason, expected=plan.expected_behavior, observations=[observation], offset=offset)
    point["policy_denials"] = list(session.policy.denials)
    result.logs = [{"type": "observation", **observation}, point]
    point["short_reason"] = short_failure_reason(point)
    result.observed_behavior = point["short_reason"]
    return result
