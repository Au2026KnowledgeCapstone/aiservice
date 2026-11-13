"""Step 5 checkpoint: the full prepare -> execute -> close path through the
Engine protocol, with generate_plan wired into the run loop."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from engines.base import Credentials, CriterionSnapshot, RequirementSnapshot, Target
from engines.registry import build_engine

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


def _start_fixture_server():
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
    return server, worker


def _stop_fixture_server(server, worker):
    try:
        server.shutdown()
        worker.join()
        server.server_close()
    except OSError:
        pass  # already stopped by the test itself


@pytest.fixture
def fixture_server():
    server, worker = _start_fixture_server()
    yield server
    _stop_fixture_server(server, worker)


@pytest.fixture
def fixture_url(fixture_server):
    yield f"http://127.0.0.1:{fixture_server.server_port}"


class FakeSink:
    def __init__(self):
        self._next = 0
        self.screenshots = {}
        self.texts = {}

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


class FakeProvider:
    async def structured(self, *, operation, system, user, schema, images=()):
        if operation == "GoalPlan":
            return {
                "objective": "Reveal the secret", "preconditions": [], "expected_behavior": "Secret appears",
                "verification_criteria": ["Secret is visible"], "priority": "medium", "testability": "automatable",
                "steps": [{"action": "click", "purpose": "activate", "intent": "reveal the secret", "target_description": "Reveal Secret button", "expected_change": "content"}],
            }
        assert operation == "GoalAssessment", f"unexpected AI call: {operation}"
        payload = json.loads(user)
        last_observation_id = payload["observations"][-1]["observation_id"]
        return {
            "summary": "The secret became visible after the click.",
            "criteria": [{"criterion_index": 0, "status": "satisfied", "explanation": "Secret text appeared.", "evidence_ids": [last_observation_id]}],
        }


def requirement():
    return RequirementSnapshot(
        id="req-1", title="Reveal secret", description="Clicking Reveal Secret shows the secret text",
        criteria=(CriterionSnapshot(id="crit-1", text="Secret becomes visible after clicking Reveal Secret"),),
    )


async def test_prepare_execute_close_produces_a_passing_outcome_with_real_evidence(fixture_url):
    engine = build_engine("mvp", provider=FakeProvider())
    session = await engine.prepare(Target(fixture_url, 1280, 800), Credentials({}))
    sink = FakeSink()
    try:
        outcome = await engine.execute(session, requirement(), sink)
    finally:
        await session.close()
    await engine.close()

    assert outcome.verdict == "pass"
    assert outcome.classification == "criteria_satisfied"
    assert len(outcome.criterion_verdicts) == 1
    verdict = outcome.criterion_verdicts[0]
    assert verdict.criterion_id == "crit-1"
    assert verdict.verdict == "pass"
    assert verdict.source == "ai"
    assert verdict.cited_evidence_ids, "the satisfied criterion must cite real evidence"
    assert set(verdict.cited_evidence_ids) <= sink.issued_ids()
    assert outcome.actions
    for action in outcome.actions:
        if action.evidence_id is not None:
            assert action.evidence_id in sink.issued_ids()


async def test_target_becoming_unreachable_during_execute_returns_inconclusive(fixture_server):
    # prepare() succeeds against a live server; the server is then stopped so the
    # per-requirement environment failure happens inside execute(), not prepare() —
    # exactly the case the "engine returns a value, it does not raise" guarantee covers.
    url = f"http://127.0.0.1:{fixture_server.server_port}"
    engine = build_engine("mvp", provider=FakeProvider())
    session = await engine.prepare(Target(url, 1280, 800), Credentials({}))
    fixture_server.shutdown()
    fixture_server.server_close()  # close the socket so the next connection is refused promptly
    sink = FakeSink()
    try:
        outcome = await engine.execute(session, requirement(), sink)
    finally:
        await session.close()
    await engine.close()

    assert outcome.verdict == "inconclusive"
    assert outcome.classification == "environment_failure"
    assert all(v.verdict == "inconclusive" for v in outcome.criterion_verdicts)
