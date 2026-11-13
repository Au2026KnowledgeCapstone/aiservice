"""Step 5 checkpoint (planner.py): generate_plan validates the AI's proposal
before it can run, and always sends allow_writes=False."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from engines.base import Credentials, Target
from engines.mvp.browser import open_session
from engines.mvp.errors import EngineError
from engines.mvp.planner import generate_plan

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_CAPABILITY_TEST") != "1", reason="Opt-in real browser test")

FIXTURE_HTML = b"<html><body><button>Reveal Secret</button></body></html>"


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


class RecordingProvider:
    def __init__(self, response):
        self.response = response
        self.sent_execution_policy = None

    async def structured(self, *, operation, system, user, schema, images=()):
        self.sent_execution_policy = json.loads(user)["execution_policy"]
        return self.response


VALID_PLAN = {
    "objective": "Reveal the secret", "preconditions": [], "expected_behavior": "Secret appears",
    "verification_criteria": ["Secret is visible"], "priority": "medium", "testability": "automatable",
    "steps": [{"action": "click", "purpose": "activate", "intent": "reveal the secret", "target_description": "Reveal Secret button", "expected_change": "content"}],
}


async def test_generate_plan_always_forbids_writes(fixture_url):
    session = await open_session(Target(fixture_url, 1280, 800), Credentials({}))
    try:
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            observation = await session.observe(page)
            provider = RecordingProvider(VALID_PLAN)
            plan = await generate_plan(provider, {"id": "r1", "acceptance_criteria": ["x"]}, observation, fixture_url)
            assert provider.sent_execution_policy == {"allow_writes": False, "allowed_write_urls": [], "max_actions": 15}
            assert plan.steps[0].action == "click"
        finally:
            await context.close()
    finally:
        await session.close()


async def test_generate_plan_rejects_a_multi_field_instruction(fixture_url):
    session = await open_session(Target(fixture_url, 1280, 800), Credentials({}))
    try:
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            observation = await session.observe(page)
            bad_plan = {
                **VALID_PLAN,
                "steps": [{"action": "fill", "purpose": "enter_text", "intent": "fill all fields", "target_description": "every input field", "value": "x", "expected_change": "none"}],
            }
            provider = RecordingProvider(bad_plan)
            with pytest.raises(EngineError):
                await generate_plan(provider, {"id": "r1", "acceptance_criteria": ["x"]}, observation, fixture_url)
        finally:
            await context.close()
    finally:
        await session.close()
