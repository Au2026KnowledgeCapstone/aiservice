"""Observe -> select an existing ID -> act once -> record -> assess intent.

Ported from BlindSpot/backend/app/services/browser/goal_executor.py — this is
the real executor the A4 handoff's narrative describes (see the module
docstring history in the A4 handoff notes: `executor.py`'s inline loop is
dead code for the current product; `planner.generate_plan` always returns a
`GoalPlan`, which always routes here).

Screenshots go through the injected EvidenceSink instead of a filesystem
`storage`/`prefix`. `screenshot_bytes` keeps every captured PNG in memory for
the duration of this one call, keyed by the evidence id the sink returned —
needed because vision assessment re-reads a handful of these bytes later in
the same run, and the sink has no read path back (aiservice stores evidence
for BE to fetch later, not for the engine to re-fetch from itself).
"""
import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

from engines.mvp.action_contracts import compile_action, observed_navigation_control, verify_action
from engines.mvp.assessment import assess_goal
from engines.mvp.checkpoints import change_kind, destination_matches, page_state, region_content, transition_reached, transition_step, wait_for_transition
from engines.mvp.defaults import AI_VISION_ENABLED, MAX_ACTIONS, SCENARIO_TIMEOUT_SECONDS
from engines.mvp.diagnostics import assessment_failures, explain_error, failure_point, safe_endpoint, short_failure_reason
from engines.mvp.errors import EngineError, PolicyDenied
from engines.mvp.execution import Execution
from engines.mvp.objective import run_check
from engines.mvp.planner import choose_element
from engines.mvp.policy import origin, safe_url
from engines.mvp.repeatability import control_identity
from engines.mvp.vision import diagnose_visual, load_screenshots

logger = logging.getLogger("aiservice.mvp")


async def execute_goal(session, page, plan, requirement, provider, sink, on_progress=None):
    result = Execution()
    observations = []
    network = []
    console = []
    pending_requests = set()
    screenshot_bytes = {}
    objective_results = []
    checks = [check for check in session.configuration.objective_checks if check.requirement_id == requirement.get("id")]
    for check in checks:
        if check.criterion_index >= len(requirement["acceptance_criteria"]) or requirement["acceptance_criteria"][check.criterion_index] != check.criterion_text:
            raise EngineError("An objective check references an outdated criterion. Update browser setup before testing.")
    bindings = getattr(session, "control_bindings", {})
    active_event = None
    stage = "execution_setup"
    recording_start = getattr(session, "recording_started_at", None)

    def offset():
        return round(max(0, time.monotonic() - recording_start), 3) if recording_start is not None else None

    def network_context(request, url=None):
        return {
            "endpoint": safe_endpoint(url or getattr(request, "url", ""), session.redactor), "method": getattr(request, "method", "unknown"),
            "resource_type": request.resource_type, "step_index": active_event.get("step_index") if active_event else None,
            "video_offset_seconds": offset(), "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def on_response(response):
        if response.status >= 400 and response.request.resource_type in {"document", "xhr", "fetch", "script"} and len(network) < 20:
            network.append({
                **network_context(response.request, response.url), "origin": safe_url(response.url), "status": response.status,
                "source": "blindspot_proxy" if getattr(response, "headers", {}).get("x-blindspot-proxy-error") else "http_response",
            })

    def on_request(request):
        if request.resource_type in {"document", "xhr", "fetch", "script"}:
            pending_requests.add(request)

    def on_finished(request):
        pending_requests.discard(request)

    def on_failure(request):
        pending_requests.discard(request)
        if request.resource_type in {"document", "xhr", "fetch", "script"} and len(network) < 20:
            failure = getattr(request, "failure", "") or ""
            code = next((item for item in ("ERR_CONNECTION_REFUSED", "ERR_CONNECTION_RESET", "ERR_NAME_NOT_RESOLVED", "ERR_TIMED_OUT", "ERR_ABORTED", "ERR_BLOCKED_BY_CLIENT") if item in failure), "unavailable")
            network.append({**network_context(request), "origin": safe_url(request.url), "failure": "Browser request failed", "failure_code": code})

    def on_console(message):
        if message.type == "error" and len(console) < 20:
            console.append(session.redactor.text(message.text, 500))

    page.on("request", on_request)
    page.on("requestfinished", on_finished)
    page.on("response", on_response)
    page.on("requestfailed", on_failure)
    page.on("console", on_console)

    async def progress():
        if on_progress:
            await on_progress(result.logs)

    async def capture(step_index, kind, goal=None):
        observation = None
        for attempt in range(3):
            observation = await session.observe(page)
            observation.update(
                step_index=step_index, capture_kind=kind,
                capture_goal=session.redactor.text(goal or (active_event or {}).get("intent") or plan.objective, 500),
                capture_attempt=attempt + 1,
            )
            if not observations:
                observation["prior_session_setup"] = dict(session.auth_evidence)
            observation["timestamp"] = datetime.now(timezone.utc).isoformat()
            observation["video_offset_seconds"] = offset()
            duplicate = None
            try:
                await page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                png = await page.screenshot(full_page=True, mask=[page.locator('input[type="password"],input[type="email"]')], timeout=5000)
                evidence_id = await sink.put_screenshot(png, step_index=step_index)
                screenshot_bytes[evidence_id] = png
                result.media.append({"evidence_type": "screenshot", "evidence_id": evidence_id, "step_index": step_index})
                observation.update(screenshot_captured=True, screenshot_key=evidence_id)
                observation["screenshot_hash"] = hashlib.sha256(png).hexdigest()
                duplicate = next((item for item in reversed(observations) if item.get("screenshot_hash") == observation["screenshot_hash"] and item.get("evidence_integrity") == "verified"), None)
                if duplicate:
                    observation["duplicate_of"] = duplicate["observation_id"]
            except asyncio.CancelledError:
                raise
            except Exception:
                observation.update(screenshot_captured=False, screenshot_error="Browser screenshot capture was unavailable.")
            try:
                consistent = await session.capture_is_consistent(page, observation)
            except asyncio.CancelledError:
                raise
            except Exception:
                consistent = False
            if duplicate and observation.get("visual_content_hash") and duplicate.get("visual_content_hash") != observation["visual_content_hash"]:
                consistent = False
                observation["integrity_issue"] = "identical_image_for_different_page_content"
            observation["evidence_integrity"] = "verified" if consistent else "unstable"
            observations.append(observation)
            result.logs.append({"type": "observation", **observation})
            await progress()
            if consistent:
                return observation
            if attempt < 2:
                await session.wait_ready(page)
        if observation.get("integrity_issue"):
            raise EngineError("The screenshot did not show a new visual state after the expected data change; visual evidence could not be verified.")
        raise EngineError("The page changed during screenshot capture; no consistent checkpoint could be recorded.")

    async def objective_checks(step_index):
        for check in checks:
            if check.after_step != step_index:
                continue
            event = {
                "type": "objective_check", "criterion_index": check.criterion_index, "kind": check.kind, "step_index": step_index,
                "status": "inconclusive", "evidence_ids": [], "expected": session.redactor.text(check.expected, 2000),
                "target": check.target.__dict__ if check.target else None,
            }
            before = observations[-1]["observation_id"]
            try:
                passed = await run_check(page, check, session.policy.target_origin)
                await session.wait_ready(page)
                after = await capture(step_index if step_index is not None else len(plan.steps) - 1, "verification", check.criterion_text)
                confirmed = await run_check(page, check, session.policy.target_origin)
                if confirmed != passed or not await session.capture_is_consistent(page, after):
                    raise EngineError("The objective check or page state changed while its screenshot was captured; the criterion could not be verified.")
                event.update(
                    status="satisfied" if passed else "violated",
                    evidence_ids=list(dict.fromkeys([ref for ref in [active_event.get("before_observation_id") if active_event else None, before, after["observation_id"]] if ref])),
                    explanation="Configured objective check " + ("passed." if passed else "did not match the expected result."),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                event["explanation"] = session.redactor.text(explain_error(exc, "objective_check"), 2000)
            objective_results.append(event)
            result.logs.append(event)
            await progress()

    async def record_failure(exc):
        reason = session.redactor.text(explain_error(exc, stage), 2000)
        event = active_event
        if stage == "network_validation" and network:
            event = next((item for item in result.logs if item.get("action") and item.get("step_index") == network[0].get("step_index")), event)
        index = event.get("step_index") if event else None
        related = []
        if event:
            before = event.get("before_observation_id")
            related = [item for item in observations if item["observation_id"] == before]
        try:
            checkpoint = await asyncio.wait_for(capture(index if index is not None else -1, "failure"), 8)
            related.append(checkpoint)
        except asyncio.CancelledError:
            raise
        except Exception:
            png = None
            try:
                png = await page.screenshot(full_page=True, mask=[page.locator('input[type="password"],input[type="email"]')], timeout=5000)
            except Exception:
                pass
            checkpoint = {
                "observation_id": f"failure-{len(result.logs)}", "step_index": index, "accessible_ui": "", "capture_kind": "failure",
                "evidence_integrity": "unverified", "capture_goal": session.redactor.text((event or {}).get("intent") or plan.objective, 500),
                "url": safe_endpoint(page.url, session.redactor), "video_offset_seconds": offset(), "screenshot_captured": False,
            }
            if png:
                evidence_id = await sink.put_screenshot(png, step_index=index)
                screenshot_bytes[evidence_id] = png
                checkpoint.update(screenshot_captured=True, screenshot_key=evidence_id)
                result.media.append({"evidence_type": "screenshot", "evidence_id": evidence_id, "step_index": index})
            else:
                checkpoint["screenshot_error"] = "Page unavailable; failure screenshot could not be captured."
            observations.append(checkpoint)
            result.logs.append({"type": "observation", **checkpoint})
            related.append(checkpoint)
        point = failure_point(
            stage=stage, reason=reason, expected=event.get("intent") if event else plan.expected_behavior, step=index, event=event,
            observations=related, offset=network[0].get("video_offset_seconds") if stage == "network_validation" and network else event.get("failure_video_offset_seconds", offset()) if event else offset(),
        )
        if stage == "network_validation":
            point["network_errors"] = list(network)
            detail = "; ".join(
                f"{item['method']} {item['endpoint']}: HTTP {item['status']} ({item.get('source', 'http_response')})" if "status" in item else f"{item['method']} {item['endpoint']}: request failed"
                for item in network[:5]
            )
            if detail:
                point["reason"] += " " + detail
        critical = [item for item in session.policy.denials if item.get("critical")]
        if critical:
            detail = "; ".join(f"{item['origin']}: {item['reason']}" for item in critical[:5])
            point["reason"] += " Policy/proxy observations: " + detail
        if event and event.get("navigation"):
            point["navigation"] = dict(event["navigation"])
        if session.policy.denials:
            point["policy_denials"] = list(session.policy.denials)
        point["short_reason"] = short_failure_reason(point)
        result.logs.append(point)
        return point["short_reason"]

    try:
        async with asyncio.timeout(SCENARIO_TIMEOUT_SECONDS):
            session.discovery_hint = ""
            stage = "page_readiness"
            await session.wait_ready(page)
            result.logs.append({"type": "readiness", "status": "passed", "step_index": -1})
            await capture(-1, "initial")
            if not plan.steps:
                raise EngineError("The plan contains no executable evidence-gathering steps.")
            if len(plan.steps) > min(15, MAX_ACTIONS):
                raise EngineError("Action limit exceeded.")
            for index, step in enumerate(plan.steps):
                if origin(page.url) != session.policy.target_origin:
                    raise PolicyDenied("Page left target origin")
                event = {
                    "step_index": index, "action": step.action, "intent": session.redactor.text(step.intent, 1000),
                    "timestamp": datetime.now(timezone.utc).isoformat(), "status": "running",
                }
                active_event = event
                event["video_offset_seconds"] = offset()
                result.logs.append(event)
                await progress()
                try:
                    target = None
                    selected = None
                    observation = observations[-1]
                    event["before_observation_id"] = observations[-1]["observation_id"]
                    if step.action in {"click", "fill", "select", "check", "press", "wait"}:
                        # Fresh IDs for every selection; old page references are never reused.
                        stage = "element_selection"
                        session.discovery_hint = step.target_description or step.intent
                        observation = await capture(index, "before_action")
                        saved_identity = bindings.get(str(index))
                        if saved_identity:
                            matches = [item for item in observation["controls"] if control_identity(item) == saved_identity]
                            if len(matches) != 1:
                                raise EngineError("Saved control is missing or ambiguous. No alternate control was selected. Review the page and regenerate the saved plan if needed.")
                            element_id = matches[0]["element_id"]
                            event["selection_method"] = "saved_control"
                        else:
                            stage = "ai_element_selection"
                            choice = await choose_element(provider, step, observation)
                            stage = "element_selection"
                            if not choice.element_id:
                                controls = observation["controls"]
                                if not controls:
                                    detail = "No controls were found on the page."
                                elif choice._selection_method == "unavailable" and any(item.get("ambiguous") for item in controls):
                                    detail = "Multiple controls match the requested target."
                                else:
                                    detail = "No unambiguous observed control matches the planned action."
                                raise EngineError(detail)
                            element_id = choice.element_id
                            event["selection_method"] = choice._selection_method
                        target = await session.resolve_element(element_id)
                        selected = next(item for item in observation["controls"] if item["element_id"] == element_id)
                        event.update(element_id=element_id, target_identity=control_identity(selected), before_observation_id=observation["observation_id"])
                    if step.action == "navigate":
                        observation = await capture(index, "before_action")
                        event["before_observation_id"] = observation["observation_id"]
                        stage = "contract_validation"
                        selected = observed_navigation_control(step, observation, observations, requirement, session.configuration, session.target_url)
                        if selected:
                            target = await session.resolve_element(selected["element_id"])
                            event.update(element_id=selected["element_id"], target_identity=control_identity(selected), selection_method="observed_navigation_control")
                    stage = "contract_validation"
                    observation["form_entry_contracts"] = getattr(session, "form_entry_contracts", [])
                    bound = compile_action(step, selected, observation, observations, requirement, session.configuration, session.target_url, index)
                    step = bound.step
                    contract_record = bound.record()
                    if contract_record.get("expected_destination"):
                        contract_record["expected_destination"] = safe_endpoint(contract_record["expected_destination"], session.redactor)
                    event.update(action=step.action, action_contract=contract_record)
                    if bound.purpose == "navigate" and bound.expected:
                        event["navigation"] = {"status": "not_confirmed", "page_ready": False, "expected_url": safe_endpoint(bound.expected, session.redactor), "actual_url": safe_endpoint(page.url, session.redactor)}
                    transition_before = None
                    completion_step = step
                    if step.action in {"navigate", "click", "fill", "select", "check", "press", "wait"} and not await session.capture_is_consistent(page, observation):
                        raise EngineError("The page changed after the before-action screenshot; no action was taken.")
                    if change_kind(step) != "none":
                        transition_before = await page_state(page)
                        completion_step = transition_step(step, transition_before)
                        if completion_step.change_region:
                            region_content(transition_before, completion_step.change_region)
                    stage = "browser_action"
                    if step.action == "navigate":
                        if not step.url:
                            raise EngineError("Navigation step has no URL.")
                        url = urljoin(session.target_url, step.url)
                        await session.policy.check(url, "GET", True)
                        completion_step = step.model_copy(update={"url": url, "change_region": None})
                        event["navigation"] = {"status": "not_confirmed", "page_ready": False, "expected_url": safe_endpoint(url, session.redactor), "actual_url": safe_endpoint(page.url, session.redactor)}
                        response = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        event["navigation"] = {"status": "arrived" if destination_matches(url, page.url) else "not_confirmed", "page_ready": False, "expected_url": safe_endpoint(url, session.redactor), "actual_url": safe_endpoint(page.url, session.redactor)}
                        if response and response.status >= 400:
                            raise EngineError("Navigation returned an HTTP error.")
                    elif step.action == "click":
                        await target.click()
                    elif step.action in {"fill", "select", "press"}:
                        if step.value is None:
                            raise EngineError("Action requires a value.")
                        # Credential entry is confined to the non-recorded login context.
                        if step.action == "fill":
                            if await target.get_attribute("type") in {"password", "email"}:
                                raise PolicyDenied("Credential entry is not allowed during recorded analysis")
                            await target.fill(step.value)
                        elif step.action == "select":
                            await target.select_option(label=step.value)
                        else:
                            if step.value not in {"Enter", "Tab", "Escape", "Space", "ArrowDown", "ArrowUp"}:
                                raise PolicyDenied("Unsupported key")
                            await target.press(step.value)
                    elif step.action == "check":
                        if step.checked is False:
                            await target.uncheck()
                        else:
                            await target.check()
                    elif step.action == "wait":
                        await target.wait_for(state="visible")
                    elif step.action == "read":
                        content = await capture(index, "read")
                        event["observation"] = content.get("accessible_ui", "")
                        event["read_scope"] = "observed_page_content"
                    elif step.action not in {"observe", "screenshot"}:
                        raise PolicyDenied("Unsupported action")
                    event["status"] = "passed"
                    stage = "page_readiness"
                    if transition_before is not None:
                        stage = "state_transition"
                        event["transition"] = await wait_for_transition(page, transition_before, completion_step, is_pending=lambda: bool(pending_requests))
                        if event.get("navigation"):
                            event["navigation"].update(status="arrived" if destination_matches(completion_step.url, page.url) else "not_confirmed", actual_url=safe_endpoint(page.url, session.redactor))
                        stage = "page_readiness"
                    await session.wait_ready(page)
                    result.logs.append({"type": "readiness", "status": "passed", "step_index": index, "basis": "visible_page_state", "pending_requests_observed": bool(pending_requests)})
                    stage = "observation_capture"
                    after = await capture(index, "destination" if event.get("transition", {}).get("page_changed") else "after_action")
                    if event.get("transition"):
                        previous = next((item for item in observations if item["observation_id"] == event.get("before_observation_id")), {})
                        if event["transition"].get("kind") in {"content", "view"} and after.get("screenshot_hash") and after["screenshot_hash"] == previous.get("screenshot_hash"):
                            after["evidence_integrity"] = "unverified"
                            result.logs[-1]["evidence_integrity"] = "unverified"
                            raise EngineError("The screenshot did not show a new visual state after the expected data change; visual evidence could not be verified.")
                        final = await page_state(page)
                        if not transition_reached(transition_before, final, completion_step) or final["state_hash"] != after["page_state_hash"]:
                            after["evidence_integrity"] = "unverified"
                            result.logs[-1]["evidence_integrity"] = "unverified"
                            stage = "state_transition"
                            raise EngineError("The resulting page changed again before evidence was confirmed; the data change could not be verified.")
                        after["transition"] = event["transition"]
                        result.logs[-1]["transition"] = event["transition"]
                    if event.get("transition", {}).get("page_changed"):
                        result.logs.append({
                            "type": "verification_context", "step_index": index, "status": "observed", "observation_id": after["observation_id"], "url": after["url"],
                            "observed_regions": [item["name"] for item in after.get("content_regions", [])],
                            "message": "Destination inspected from a fresh checkpoint; source-page regions were discarded.", "goal": session.redactor.text(step.intent, 500),
                        })
                    event["after_observation_id"] = after["observation_id"]
                    stage = "action_verification"
                    event["action_contract"]["result"] = await verify_action(bound, session, page, after, observation)
                    if event.get("navigation"):
                        event["navigation"]["page_ready"] = True
                    await objective_checks(index)
                except Exception as exc:
                    event["status"] = "inconclusive"
                    if stage == "contract_validation":
                        event["action_contract"] = {**event.get("action_contract", {}), "status": "rejected", "action_taken": False}
                    if event.get("navigation"):
                        event["navigation"].update(status="arrived" if destination_matches(completion_step.url, page.url) else "not_confirmed", actual_url=safe_endpoint(page.url, session.redactor))
                    event["reason"] = session.redactor.text(explain_error(exc, stage), 2000)
                    event["failure_video_offset_seconds"] = offset()
                    raise
                finally:
                    await progress()
            await objective_checks(None)
            for check in checks:
                if check.after_step is not None and check.after_step >= len(plan.steps):
                    missing = {
                        "type": "objective_check", "criterion_index": check.criterion_index, "kind": check.kind, "step_index": check.after_step,
                        "status": "inconclusive", "evidence_ids": [], "explanation": "Configured checkpoint is outside the saved plan. Update the check or regenerate the plan.",
                    }
                    objective_results.append(missing)
                    result.logs.append(missing)
            stage = "network_validation"
            if any(item["critical"] for item in session.policy.denials):
                raise PolicyDenied("Critical browser requests were blocked")
            if network:
                result.classification = "environment_failure"
                raise EngineError("Relevant network requests failed; goal compliance cannot be assessed reliably.")
        # Assessment runs only after completed interactions, not after locator/network failure.
        stage = "requirement_assessment"
        result.logs.append({"type": "assessment_status", "messages": ["Comparing observed behavior with requirement intent."]})
        await progress()
        actions = [entry for entry in result.logs if "action" in entry]
        images, skipped = load_screenshots(screenshot_bytes, observations, actions=actions) if AI_VISION_ENABLED else ([], [])
        result.logs.append({"type": "visual_inputs", "observation_ids": [image.observation_id for image in images], "skipped": skipped, "enabled": AI_VISION_ENABLED})
        outcome, classification, assessment = await assess_goal(provider, requirement, plan, observations, actions, images=images, objective_results=objective_results)
        stage = "network_validation"
        if any(item["critical"] for item in session.policy.denials):
            raise PolicyDenied("Critical browser request blocked during assessment")
        if network:
            result.classification = "environment_failure"
            raise EngineError("Relevant network requests failed during assessment; no product verdict was accepted.")
        result.outcome = outcome
        result.classification = classification
        count = sum(item.status == "satisfied" for item in assessment.criteria)
        result.observed_behavior = f"{count}/{len(assessment.criteria)} acceptance criteria satisfied. " + session.redactor.text(assessment.summary, 3000)
        safe_assessment = assessment.model_dump()
        safe_assessment["summary"] = session.redactor.text(safe_assessment["summary"], 3000)
        for item in safe_assessment["criteria"]:
            item["explanation"] = session.redactor.text(item["explanation"], 2000)
        result.logs.append({"type": "goal_assessment", "assessment": safe_assessment, "contract_version": "goal-assessment-v3", "visual_evidence_ids": [image.observation_id for image in images]})
        # Explanations were redacted before generating the public failure breakdown.
        for criterion, safe in zip(assessment.criteria, safe_assessment["criteria"]):
            criterion.explanation = safe["explanation"]
        points = assessment_failures(assessment, requirement, observations, actions)
        result.logs.extend(points)
        if outcome == "INCONCLUSIVE" and points:
            result.observed_behavior = next((item["short_reason"] for item in points if item["status"] == "inconclusive"), points[0]["short_reason"])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Browser execution could not verify the requirement", exc_info=True, extra={"event": "browser.execution_failed", "stage": stage, "step_index": active_event.get("step_index") if active_event else None})
        result.outcome = "INCONCLUSIVE"
        if isinstance(exc, PolicyDenied):
            result.classification = "policy_blocked"
        elif stage == "requirement_assessment":
            result.classification = "assessment_failure"
        elif stage == "ai_element_selection":
            result.classification = "ai_execution_failure"
        elif stage == "contract_validation":
            result.classification = "invalid_action_contract"
        elif stage == "action_verification":
            result.classification = "action_result_unverified"
        result.observed_behavior = await record_failure(exc)
        if AI_VISION_ENABLED and stage not in {"requirement_assessment", "ai_element_selection", "contract_validation"}:
            images, skipped = load_screenshots(screenshot_bytes, observations)
            if images:
                try:
                    diagnostic = next(entry for entry in reversed(result.logs) if entry.get("type") == "failure_point")
                    diagnosis = await diagnose_visual(provider, requirement, images, observations, diagnostic)
                    safe = diagnosis.model_dump()
                    safe["reasoning"] = session.redactor.text(safe["reasoning"], 2000)
                    safe["suggested_next_step"] = session.redactor.text(safe["suggested_next_step"], 1000)
                    safe["observed_facts"] = [session.redactor.text(fact, 1000) for fact in safe["observed_facts"]]
                    if safe["kind"] == "missing_test_data" and (network or any(item.get("critical") for item in session.policy.denials)):
                        safe["kind"] = "insufficient_evidence"
                        safe["reasoning"] = "The view appears empty, but network/policy failures prevent establishing that records are missing. " + safe["reasoning"]
                    result.logs.append({"type": "visual_diagnosis", "diagnosis": safe, "visual_evidence_ids": [image.observation_id for image in images]})
                    if safe["kind"] == "missing_test_data" and stage == "element_selection" and not network and not any(item.get("critical") for item in session.policy.denials):
                        result.classification = "possible_missing_test_data"
                    # Visual inference stays in stored logs and cannot replace the observed failure.
                except asyncio.CancelledError:
                    raise
                except Exception as visual_error:
                    detail = str(visual_error) if isinstance(visual_error, EngineError) else "Screenshot interpretation could not be completed."
                    result.logs.append({"type": "visual_diagnosis_unavailable", "messages": [session.redactor.text(detail, 1000), "The original execution failure and evidence were retained."]})
            else:
                result.logs.append({"type": "visual_diagnosis_unavailable", "messages": ["No valid bounded screenshot was available for visual interpretation."], "skipped": skipped})
        elif AI_VISION_ENABLED and stage == "contract_validation":
            result.logs.append({"type": "visual_diagnosis_unavailable", "messages": ["The action contract was rejected before interaction; no additional AI request was made."]})
        elif AI_VISION_ENABLED:
            result.logs.append({"type": "visual_diagnosis_unavailable", "messages": ["An AI request failed. No additional visual request was attempted; original evidence is retained."]})
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("requestfinished", on_finished)
        page.remove_listener("response", on_response)
        page.remove_listener("requestfailed", on_failure)
        page.remove_listener("console", on_console)
        if console:
            result.logs.append({"type": "console_errors", "messages": console})
        if network:
            result.logs.append({"type": "network_errors", "requests": network})
        if session.policy.denials:
            result.logs.append({"type": "policy_denials", "requests": list(session.policy.denials)})
        await progress()
    return result
