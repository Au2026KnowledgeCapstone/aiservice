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
