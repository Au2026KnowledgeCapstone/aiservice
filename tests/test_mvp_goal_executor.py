"""Step 4 checkpoint: a run produces evidence ids from the sink, every cited id
exists, and instability during a screenshot capture retries only the capture."""
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


@pytest.fixture
def fixture_url():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(FIXTURE_HTML)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    worker.join()
    server.server_close()


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


def plan():
    return GoalPlan(
        objective="Verify the secret becomes visible after clicking Reveal Secret",
        preconditions=[], expected_behavior="The secret text appears after clicking the button",
        verification_criteria=["Secret text is visible after the click"], priority="medium", testability="automatable",
        steps=[GoalStep(action="click", purpose="activate", intent="reveal the secret", target_description="Reveal Secret button", expected_change="content")],
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
