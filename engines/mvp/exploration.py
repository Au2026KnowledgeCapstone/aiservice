"""Bounded, read-only exploration for planning; discoveries are never test verdicts.

Ported from BlindSpot/backend/app/services/browser/exploration.py. The
reference writes screenshots to a filesystem path and additionally toggles
`session.policy.writes` off for the duration of exploration (on top of its
candidate heuristics already excluding write-looking controls). Here,
screenshots go through the injected EvidenceSink instead — `screenshot_key`
below holds the evidence id the sink returned, not a storage path. And
NetworkPolicy here has no write-allowlist to toggle at all (see policy.py and
the A4 handoff notes: there is no BrowserConfiguration channel to populate
one from) — read-only-ness during exploration rests entirely on the
candidate-selection heuristics below, not on an enforced network policy.
"""
import asyncio
import hashlib
import re

from engines.mvp.form_entries import is_form_entry
from engines.mvp.goals import GoalStep
from engines.mvp.policy import origin
from engines.mvp.repeatability import control_identity, digest
from engines.mvp.checkpoints import page_state, wait_for_transition

VERSION = "scoped-discovery-v3"
STOP = re.compile(r"\b(add|create|new|save|submit|send|delete|remove|pay|purchase|logout|log out|sign out|edit|cancel|approve|reject|upload|download)\b", re.I)
NOISE = set("the a an and or of to for in on from with that this selected selecting show shows displays display opens opening returns initially each supplied detail details page view button patient provided".split())


def words(text):
    return set(re.findall(r"[^\W_]+", str(text).casefold())) - NOISE


def candidates(observation, requirement, visited, target_url):
    documented = " ".join(str(requirement.get(k, "")) for k in ("title", "description", "acceptance_criteria"))
    query = words(documented)
    entity_words = set(re.findall(r"[^\W_]+", documented.casefold()))
    choices = []
    confirms = [c for c in observation.get("controls", []) if not c.get("disabled") and not c.get("has_form") and re.match(r"^confirm selection\b", c.get("name", ""), re.I)]
    group_cards = [c for c in observation.get("controls", []) if c.get("custom_clickable") and c.get("selection_group") and c.get("selection_confirmation")]
    selection_relevant = bool({"account", "patient", "selection", "selected"} & entity_words)
    for item in observation.get("controls", []):
        label = item.get("name", "")
        role = item.get("role")
        href = item.get("href")
        if role not in {"button", "link", "tab"} or item.get("disabled") or item.get("blocked_by_modal") or item.get("ambiguous") or not item.get("actionable", True):
            continue
        entry = is_form_entry(item)
        if (STOP.search(label) and not entry) or item.get("has_form") and item.get("input_type") == "submit":
            continue
        if href:
            if STOP.search(href) and not entry:
                continue
            try:
                if origin(href) != origin(target_url):
                    continue
            except ValueError:
                continue
        matching = len(words(label) & query)
        if entry:
            matching += len((set(re.findall(r"[^\W_]+", label.casefold())) - {"add", "new", "create"}) & entity_words)
        record = bool(re.match(r"^log\s+\d+\b", label, re.I) and {"pain", "log", "logs", "history"} & query)
        card = bool(item.get("custom_clickable") and item.get("selection_group") and selection_relevant and not confirms)
        confirm = bool(item in confirms and group_cards and selection_relevant)
        route = bool(entry or href or re.search(r"\b(view|open|back|home|dashboard|report|reports|history)\b", label, re.I))
        if not (card or confirm or record or route and matching):
            continue
        key = digest([observation["url"], observation["visual_content_hash"], control_identity(item)])
        if key in visited:
            continue
        # Explore another entry before returning home; avoid reselecting the current log.
        if record and any(re.match(r"^" + re.escape(re.match(r"^log\s+\d+", label, re.I)[0]) + r"\s*$", r["name"], re.I) for r in observation.get("content_regions", [])):
            continue
        back = bool(re.search(r"\b(back|home|dashboard)\b", label, re.I))
        choices.append((120 if confirm else 110 if card else 100 if record else matching - (20 if back else 0), key, item))
    choices.sort(key=lambda row: (-row[0], row[2].get("name", "")))
    # Equal candidates remain unexplored rather than choosing an arbitrary duplicate.
    if len(choices) > 1 and choices[0][0] == choices[1][0] and control_identity(choices[0][2]) == control_identity(choices[1][2]):
        return []
    return choices


def planning_map(record):
    return {
        "version": VERSION, "status": record["status"],
        "scope": "Partial observed application map; not acceptance evidence or complete coverage.",
        "states": [
            {
                **{k: s.get(k) for k in ("observation_id", "url", "accessible_ui", "content_regions")},
                "controls": [
                    {k: c.get(k) for k in (
                        "role", "name", "href", "scope", "checked", "expanded", "element", "input_type",
                        "editable", "readonly", "has_form", "options", "placeholder", "label",
                        "custom_clickable", "selection_group", "selection_confirmation", "interaction_signals",
                    )}
                    for c in s.get("controls", [])[:40]
                ],
            }
            for s in record["states"] if s.get("evidence_integrity") == "verified"
        ],
        "transitions": record["transitions"], "unexplored": record["unexplored"],
    }


async def explore(session, page, requirement, sink, max_actions=4, timeout=35):
    record = {
        "type": "discovery_map", "version": VERSION, "status": "partial", "states": [], "transitions": [], "unexplored": [],
        "message": "Read-only discovery for planning. Actual test execution starts in a separate browser context.",
    }
    media = []
    visited = set()
    failures = []

    def response(response):
        if response.status >= 400 and response.request.resource_type in {"document", "script", "stylesheet", "xhr", "fetch"}:
            failures.append(response.status)

    page.on("response", response)

    async def capture():
        observation = await session.observe(page)
        png = await page.screenshot(full_page=True, mask=[page.locator('input[type="password"],input[type="email"]')], timeout=5000)
        evidence_id = await sink.put_screenshot(png, step_index=None)
        media.append({"evidence_type": "screenshot", "evidence_id": evidence_id, "step_index": None})
        valid = await session.capture_is_consistent(page, observation) and bool(observation.get("accessible_ui", "").strip() or observation.get("controls"))
        screenshot_hash = hashlib.sha256(png).hexdigest()
        for prior in record["states"]:
            if prior.get("screenshot_hash") == screenshot_hash and prior.get("visual_content_hash") != observation.get("visual_content_hash"):
                valid = False
        observation.update(
            type="observation", screenshot_hash=screenshot_hash, screenshot_key=evidence_id, screenshot_captured=True,
            evidence_integrity="verified" if valid else "unverified", capture_kind="discovery",
            capture_goal="Map available views and observed interaction effects before test planning.",
        )
        record["states"].append(observation)
        if not valid:
            raise ValueError("Unstable discovery checkpoint")
        return observation

    try:
        async with asyncio.timeout(timeout):
            session.discovery_hint = requirement.get("title", "")
            current = await capture()
            for _ in range(max_actions):
                choices = candidates(current, requirement, visited, session.target_url)
                if not choices:
                    break
                _, key, control = choices[0]
                visited.add(key)
                edge = {"from": current["observation_id"], "control": control_identity(control), "action": "click", "status": "unverified", "form_entry_probe": is_form_entry(control)}
                record["transitions"].append(edge)
                before = await page_state(page)
                if not await session.capture_is_consistent(page, current):
                    raise ValueError("Discovery page changed before interaction")
                prior_denials = len(session.policy.denials)
                prior_failures = len(failures)
                target = await session.resolve_element(control["element_id"])
                await target.click()
                step = GoalStep(action="click", purpose="activate", intent="Observe the effect of the selected read-only control", expected_change="content")
                await wait_for_transition(page, before, step, timeout=5)
                if origin(page.url) != session.policy.target_origin:
                    raise ValueError("Discovery left approved application")
                after = await capture()
                if failures[prior_failures:] or any(d.get("critical") for d in session.policy.denials[prior_denials:]):
                    raise ValueError("Discovery connection failed")
                old = {r["name"]: r["text"] for r in current.get("content_regions", [])}
                edge.update(
                    to=after["observation_id"], status="observed",
                    effect="navigation" if after["url"] != current["url"] else "content_update",
                    changed_regions=[r["name"] for r in after.get("content_regions", []) if old.get(r["name"]) != r["text"]],
                )
                current = after
            record["unexplored"] = [{"label": c["name"], "reason": "Outside bounded requirement-scoped exploration or not established read-only."} for c in current.get("controls", [])][:30]
    except asyncio.CancelledError:
        raise
    except Exception:
        record["unexplored"].append({"reason": "Discovery stopped at its action/time limit, an unavailable connection, or an unverified transition; no result was inferred."})
    finally:
        page.remove_listener("response", response)
        session.discovery_hint = ""
    return record, media
