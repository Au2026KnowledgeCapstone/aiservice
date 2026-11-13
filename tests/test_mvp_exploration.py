"""Step 4 checkpoint (exploration.py): bounded, read-only discovery never
selects a write-looking control, and stays within its action/time budget."""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from engines.base import Credentials, Target
from engines.mvp.browser import open_session
from engines.mvp.exploration import explore

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_CAPABILITY_TEST") != "1", reason="Opt-in real browser test")

PAGES = {
    "/": b"""<html><body>
        <a href="/profile">Open Profile</a>
        <button>Delete Patient</button>
    </body></html>""",
    "/profile": b"""<html><body><h1>Profile</h1><p>Nothing more to see.</p></body></html>""",
}


@pytest.fixture
def fixture_url():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = PAGES.get(self.path, b"<html><body>Not found</body></html>")
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


class FakeSink:
    def __init__(self):
        self._next = 0
        self.screenshots = {}

    async def put_screenshot(self, data, *, step_index=None):
        self._next += 1
        evidence_id = f"ev-{self._next}"
        self.screenshots[evidence_id] = data
        return evidence_id

    async def put_text(self, body, *, kind):
        raise AssertionError("exploration does not write text evidence")


async def test_exploration_follows_a_read_only_link_and_skips_delete(fixture_url):
    session = await open_session(Target(fixture_url, 1280, 800), Credentials({}))
    try:
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            requirement = {"id": "req-1", "title": "View Profile", "description": "A user can view their profile", "acceptance_criteria": ["Profile is visible"]}
            sink = FakeSink()
            record, media = await explore(session, page, requirement, sink, max_actions=2, timeout=15)
            transitions = record["transitions"]
            assert all(t["control"].get("locator", {}).get("name") != "Delete Patient" for t in transitions)
            assert any(t.get("effect") == "navigation" for t in transitions)
            assert media, "exploration should capture at least the initial screenshot"
            assert len(transitions) <= 2
        finally:
            await context.close()
    finally:
        await session.close()
