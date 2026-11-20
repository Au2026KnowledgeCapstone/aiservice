"""Step 4 checkpoint: a run produces evidence ids from the sink, every cited id
exists, instability during a screenshot capture retries only the capture, and a
page with *persistent* motion (a carousel) still reaches a usable checkpoint."""
import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from engines.base import Credentials, Target
from engines.mvp.browser import open_session
from engines.mvp.goal_executor import execute_goal
from engines.mvp.goals import GoalPlan, GoalStep
from engines.mvp.requirement import as_dict
from engines.base import CriterionSnapshot, RequirementSnapshot

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_CAPABILITY_TEST") != "1", reason="Opt-in real browser test")

FIXTURE_HTML = b"""
<html><body>
  <button id="reveal">Reveal Secret</button>
  <div id="secret" style="display:none">Revealed</div>
  <script>
    document.getElementById('reveal').addEventListener('click', () => {
      document.getElementById('secret').style.display = 'block';
    });
  </script>
</body></html>
"""


def _serve(body):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    worker.join()
    server.server_close()


@pytest.fixture
def fixture_url():
    yield from _serve(FIXTURE_HTML)


def carousel_html(period_ms, thumbnails):
    """A miniature of the page that triggered this bug (§2 of the bugfix
    handoff): an auto-rotating hero banner over a grid of product thumbnails.

    The banner swaps an <img src> every `period_ms` with a finite opacity
    transition for the slide effect -- motion that never stops, which is what
    the capture checkpoint used to be unable to tolerate.

    `thumbnails` sizes the page. It is not decoration: observe() works
    per-image and a full-page screenshot scales with height, so the grid widens
    the window between observe() and the consistency re-check. The bug only
    bites when a banner swap lands inside that window, and a bare local page
    captures far faster than the remote production page did.

    `period_ms` must stay well above READY_JS's 300ms quiet threshold. A faster
    flicker never lets wait_ready() resolve and fails earlier, in
    open_landing(), with "Page did not become ready" -- a different failure
    from the capture checkpoint.
    """
    return ("""
<html><body>
  <h1>Storefront</h1>
  <img id="slide" width="600" height="200" alt="banner">
  <button id="reveal">Reveal Secret</button>
  <div id="secret" style="display:none">Revealed</div>
  <div id="grid"></div>
  <script>
    const swatch = (color, w, h) => 'data:image/svg+xml;utf8,'
      + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="' + w + '" height="' + h + '">'
        + '<rect width="' + w + '" height="' + h + '" fill="' + color + '"/></svg>');
    const grid = document.getElementById('grid');
    for (let i = 0; i < __THUMBNAILS__; i++) {
      const thumb = document.createElement('img');
      thumb.width = 200; thumb.height = 200; thumb.style.display = 'block'; thumb.alt = 'product ' + i;
      thumb.src = swatch('#' + (i * 7 + 16).toString(16).padStart(2, '0') + '8844', 200, 200);
      grid.appendChild(thumb);
    }
    const slides = [swatch('#cc0000', 600, 200), swatch('#0000cc', 600, 200)];
    const image = document.getElementById('slide');
    image.src = slides[0];
    let index = 0;
    setInterval(() => {
      index = (index + 1) % 2;
      image.style.transition = 'opacity 200ms linear';
      image.style.opacity = '0.2';
      setTimeout(() => { image.src = slides[index]; image.style.opacity = '1'; }, 40);
    }, __PERIOD_MS__);
    document.getElementById('reveal').addEventListener('click', () => {
      document.getElementById('secret').style.display = 'block';
    });
  </script>
</body></html>
""".replace("__THUMBNAILS__", str(thumbnails)).replace("__PERIOD_MS__", str(period_ms))).encode()


@pytest.fixture
def carousel_url():
    """A realistic storefront banner: 2.5s rotation, modest page."""
    yield from _serve(carousel_html(2500, 24))


@pytest.fixture
def fast_carousel_url():
    """Same page, but rotating fast enough -- and tall enough to capture
    slowly -- that a banner swap lands inside every capture window. This made
    the bug reproduce 8/8 locally where the realistic page reproduced it only
    intermittently, matching the production page's much slower captures."""
    yield from _serve(carousel_html(600, 60))


class FakeSink:
    def __init__(self):
        self.screenshots = {}
        self.texts = {}
        self._next = 0

    def _id(self):
        self._next += 1
        return f"ev-{self._next}"

    async def put_screenshot(self, data, *, step_index=None):
        evidence_id = self._id()
        self.screenshots[evidence_id] = data
        return evidence_id

    async def put_text(self, body, *, kind):
        evidence_id = self._id()
        self.texts[evidence_id] = body
        return evidence_id

    def issued_ids(self):
        return set(self.screenshots) | set(self.texts)


class FakeAssessmentProvider:
    """Element selection resolves deterministically in this fixture; only the
    GoalAssessment call needs a scripted response."""

    async def structured(self, *, operation, system, user, schema, images=()):
        assert operation == "GoalAssessment", f"unexpected AI call: {operation}"
        payload = json.loads(user)
        last_observation_id = payload["observations"][-1]["observation_id"]
        return {
            "summary": "The secret became visible after the click.",
            "criteria": [{
                "criterion_index": 0, "status": "satisfied",
                "explanation": "The secret text appeared after clicking Reveal Secret.",
                "evidence_ids": [last_observation_id],
            }],
        }


def requirement():
    return as_dict(RequirementSnapshot(
        id="req-1", title="Reveal secret", description="Clicking Reveal Secret shows the secret text",
        criteria=(CriterionSnapshot(id="c1", text="Secret becomes visible after clicking Reveal Secret"),),
    ))


def plan(expected_change="content", purpose="activate"):
    return GoalPlan(
        objective="Verify the secret becomes visible after clicking Reveal Secret",
        preconditions=[], expected_behavior="The secret text appears after clicking the button",
        verification_criteria=["Secret text is visible after the click"], priority="medium", testability="automatable",
        steps=[GoalStep(action="click", purpose=purpose, intent="reveal the secret", target_description="Reveal Secret button", expected_change=expected_change)],
    )


def all_evidence_references(logs, media):
    ids = {entry["evidence_id"] for entry in media if entry.get("evidence_id")}
    for entry in logs:
        if entry.get("type") == "observation" and entry.get("screenshot_key"):
            ids.add(entry["screenshot_key"])
    return ids


async def test_run_produces_evidence_ids_that_all_exist_in_the_sink(fixture_url):
    session = await open_session(Target(fixture_url, 1280, 800), Credentials({}))
    try:
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            sink = FakeSink()
            result = await execute_goal(session, page, plan(), requirement(), FakeAssessmentProvider(), sink)
            assert result.outcome == "PASS", (result.classification, result.observed_behavior, result.logs)
            assert result.classification == "goal_satisfied"
            referenced = all_evidence_references(result.logs, result.media)
            assert referenced, "expected at least one evidence reference"
            assert referenced <= sink.issued_ids()
        finally:
            await context.close()
    finally:
        await session.close()


async def test_observation_ids_cited_in_the_assessment_are_real(fixture_url):
    session = await open_session(Target(fixture_url, 1280, 800), Credentials({}))
    try:
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            sink = FakeSink()
            result = await execute_goal(session, page, plan(), requirement(), FakeAssessmentProvider(), sink)
            observation_ids = {entry["observation_id"] for entry in result.logs if entry.get("type") == "observation"}
            assessment_log = next(entry for entry in result.logs if entry.get("type") == "goal_assessment")
            cited = {ref for criterion in assessment_log["assessment"]["criteria"] for ref in criterion["evidence_ids"]}
            assert cited <= observation_ids
        finally:
            await context.close()
    finally:
        await session.close()


async def test_unstable_capture_retries_without_reclicking(fixture_url):
    """A page state that briefly reports a running animation makes
    capture_is_consistent() fail once, forcing capture() to retry the
    screenshot — never the click that produced it."""
    session = await open_session(Target(fixture_url, 1280, 800), Credentials({}))
    try:
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            clicks = {"count": 0}
            await page.expose_binding("__countClick", lambda source: clicks.__setitem__("count", clicks["count"] + 1))
            await page.evaluate("() => document.getElementById('reveal').addEventListener('click', () => window.__countClick())")
            # Make the very first capture_is_consistent() call see a mismatched
            # hash exactly once, simulating a page caught mid-change.
            original = session.capture_is_consistent
            calls = {"count": 0}

            async def flaky(page_arg, observation):
                calls["count"] += 1
                if calls["count"] == 2:  # the 'before_action' capture inside execute_goal's loop
                    return False
                return await original(page_arg, observation)

            session.capture_is_consistent = flaky
            sink = FakeSink()
            result = await execute_goal(session, page, plan(), requirement(), FakeAssessmentProvider(), sink)
            assert result.outcome == "PASS", (result.classification, result.observed_behavior)
            assert clicks["count"] == 1, "the flaky consistency check must not cause a re-click"
        finally:
            await context.close()
    finally:
        await session.close()


async def test_persistently_moving_page_still_reaches_a_checkpoint(fast_carousel_url):
    """Regression: a page whose motion never stops (auto-rotating banner) used
    to fail the very first checkpoint with "no consistent checkpoint could be
    recorded" and take zero actions, because the whole-page state hash included
    every image's src. The run must get past the initial capture and actually
    perform its planned click.

    The step declares expected_change="none" so this stays scoped to the
    capture checkpoint: wait_for_transition is a separate mechanism with its
    own stricter settling rules and is not what this bug was about. (An
    "activate" step may only waive its expected change when the plan left the
    purpose to be inferred, hence purpose="auto".)
    """
    session = await open_session(Target(fast_carousel_url, 1280, 800), Credentials({}))
    try:
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            sink = FakeSink()
            result = await execute_goal(session, page, plan(expected_change="none", purpose="auto"), requirement(), FakeAssessmentProvider(), sink)
            assert "no consistent checkpoint could be recorded" not in (result.observed_behavior or ""), result.logs
            actions = [entry for entry in result.logs if entry.get("action") == "click" and entry.get("status") == "passed"]
            assert actions, f"no action was taken: {result.classification} / {result.observed_behavior}"
            assert result.outcome == "PASS", (result.classification, result.observed_behavior)
            # The motion was tolerated, not ignored: it is on the record.
            observations = [entry for entry in result.logs if entry.get("type") == "observation"]
            assert any(entry.get("ongoing_motion") for entry in observations), "persistent motion should be recorded on the evidence"
        finally:
            await context.close()
    finally:
        await session.close()


async def test_moving_page_completes_a_full_run_with_transition_checks(carousel_url):
    """A realistically-paced banner (2.5s) must not stop a normal run, with the
    post-action transition machinery left in play."""
    session = await open_session(Target(carousel_url, 1280, 800), Credentials({}))
    try:
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            sink = FakeSink()
            result = await execute_goal(session, page, plan(), requirement(), FakeAssessmentProvider(), sink)
            assert result.outcome == "PASS", (result.classification, result.observed_behavior)
        finally:
            await context.close()
    finally:
        await session.close()


async def test_checkpoint_tolerates_motion_but_not_broken_or_shifted_pages(fast_carousel_url):
    """The checkpoint must tolerate motion while still refusing the cases it
    exists to catch: a page that is genuinely busy, and a page whose controls
    moved under the observation the engine reasoned about."""
    session = await open_session(Target(fast_carousel_url, 1280, 800), Credentials({}))
    try:
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            observation = await session.observe(page)
            # Comfortably longer than one rotation, so the live page is
            # guaranteed to no longer match the observation's images.
            await asyncio.sleep(1.0)
            assert await session.capture_is_consistent(page, observation), "ongoing banner motion must not invalidate the checkpoint"
            assert observation.get("ongoing_motion"), "the fixture is supposed to be moving; without motion this test proves nothing"

            await page.evaluate("() => document.getElementById('reveal').setAttribute('aria-busy', 'true')")
            assert not await session.capture_is_consistent(page, observation), "a busy page must still fail the checkpoint"
            await page.evaluate("() => document.getElementById('reveal').removeAttribute('aria-busy')")

            await page.evaluate("() => document.getElementById('reveal').textContent = 'Something Else'")
            assert not await session.capture_is_consistent(page, observation), "a control that changed under the observation must still fail the checkpoint"
        finally:
            await context.close()
    finally:
        await session.close()


# --- Run #4: the observed control's DOM node is replaced during the AI call ----

REACT_RERENDER_HTML = b"""
<html><body>
  <h1>Storefront</h1>
  <div id="grid">
    <button class="card">HP Laptop</button>
    <button class="card">Lenovo Laptop</button>
  </div>
  <div id="secret" style="display:none">Added to cart</div>
  <script>
    // Delegated, so it survives the grid being re-rendered underneath it --
    // exactly like a React handler bound to the container rather than the node.
    document.getElementById('grid').addEventListener('click', event => {
      if (event.target.matches('button')) document.getElementById('secret').style.display = 'block';
    });
    // Replaces every child node with a fresh instance while rendering
    // byte-identical markup: the page looks unchanged, but any ElementHandle
    // pinned to one of those buttons is now detached. This is what a React
    // re-render of an unkeyed list does to a frozen handle.
    window.rerenderGrid = () => {
      const grid = document.getElementById('grid');
      grid.innerHTML = grid.innerHTML;
    };
    // The control is really gone, not merely re-rendered.
    window.removeHpCard = () => document.querySelectorAll('#grid button')[0].remove();
    // Two controls now share one semantic identity, so no single rebind target exists.
    window.duplicateHpCard = () => {
      const grid = document.getElementById('grid');
      const copy = document.querySelectorAll('#grid button')[0].cloneNode(true);
      grid.appendChild(copy);
    };
  </script>
</body></html>
"""


@pytest.fixture
def rerender_url():
    yield from _serve(REACT_RERENDER_HTML)


class RerenderDuringChoiceProvider:
    """Replaces the chosen control's DOM node *inside* the ElementChoice call.

    The real window is the AI round-trip: Run #4 spent ~2.2s there while the
    frozen handle sat unused. Doing the swap while the call is in flight
    reproduces that deterministically instead of racing a timer.
    """

    def __init__(self, page, mutation="() => window.rerenderGrid()"):
        self.page = page
        self.mutation = mutation
        self.operations = []

    async def structured(self, *, operation, system, user, schema, images=()):
        self.operations.append(operation)
        payload = json.loads(user)
        if operation == "ElementChoice":
            control = next(
                item for item in payload["current_page"]["controls"]
                if "HP Laptop" in (item.get("name") or "")
            )
            await self.page.evaluate(self.mutation)
            return {"element_id": control["element_id"], "explanation": "The HP Laptop card."}
        assert operation == "GoalAssessment", f"unexpected AI call: {operation}"
        return {
            "summary": "The cart confirmation appeared after the click.",
            "criteria": [{
                "criterion_index": 0, "status": "satisfied",
                "explanation": "The confirmation text appeared after clicking the product card.",
                "evidence_ids": [payload["observations"][-1]["observation_id"]],
            }],
        }


def rerender_plan():
    return GoalPlan(
        objective="Verify a product card can be opened",
        preconditions=[], expected_behavior="The cart confirmation appears after clicking a product card",
        verification_criteria=["Confirmation is visible after the click"], priority="medium", testability="automatable",
        # Vague on purpose: the cheap name heuristic must not resolve this, so
        # selection goes through the AI call that opens the window.
        steps=[GoalStep(action="click", purpose="activate", intent="open a product to add it to the cart",
                        target_description="product card in the grid", expected_change="content")],
    )


def rerender_requirement():
    return as_dict(RequirementSnapshot(
        id="req-1", title="Add a product to the cart", description="Clicking a product card confirms the addition",
        criteria=(CriterionSnapshot(id="c1", text="Confirmation becomes visible after clicking a product card"),),
    ))


async def test_control_replaced_during_the_ai_call_is_rebound_not_abandoned(rerender_url):
    """Regression: the handle frozen by observe() was validated only after the
    ElementChoice round-trip, so a re-render in that window killed the run with
    "Observed control changed or became ambiguous before interaction." The
    control is still on the page and still uniquely identifiable; the step must
    rebind to it and proceed."""
    session = await open_session(Target(rerender_url, 1280, 800), Credentials({}))
    try:
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            provider = RerenderDuringChoiceProvider(page)
            sink = FakeSink()
            result = await execute_goal(session, page, rerender_plan(), rerender_requirement(), provider, sink)

            assert "ElementChoice" in provider.operations, "the fixture must exercise the AI selection path"
            assert result.outcome == "PASS", (result.classification, result.observed_behavior)
            assert await page.evaluate("() => document.getElementById('secret').style.display") == "block", \
                "the click must have actually landed on the rebound node"
            clicks = [entry for entry in result.logs if entry.get("action") == "click"]
            assert clicks and clicks[0].get("status") == "passed", clicks
        finally:
            await context.close()
    finally:
        await session.close()


async def _run_with_mutation(url, mutation):
    session = await open_session(Target(url, 1280, 800), Credentials({}))
    try:
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            provider = RerenderDuringChoiceProvider(page, mutation)
            result = await execute_goal(session, page, rerender_plan(), rerender_requirement(), provider, FakeSink())
            return provider, result
        finally:
            await context.close()
    finally:
        await session.close()


async def test_control_removed_during_the_ai_call_still_fails(rerender_url):
    """The rebind must not become retry-until-something-works: when the chosen
    control is genuinely gone, the step fails exactly as it did before."""
    provider, result = await _run_with_mutation(rerender_url, "() => window.removeHpCard()")

    assert "ElementChoice" in provider.operations
    assert result.outcome == "INCONCLUSIVE"
    assert "selected control changed" in result.observed_behavior.lower(), result.observed_behavior


async def test_control_duplicated_during_the_ai_call_still_fails(rerender_url):
    """Two controls sharing one identity leave no single rebind target. Picking
    either would be a guess, so this must still fail."""
    provider, result = await _run_with_mutation(rerender_url, "() => window.duplicateHpCard()")

    assert "ElementChoice" in provider.operations
    assert result.outcome == "INCONCLUSIVE"
    assert "selected control changed" in result.observed_behavior.lower(), result.observed_behavior
