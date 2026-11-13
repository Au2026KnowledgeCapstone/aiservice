"""Step 7 checkpoint: credentials appear in no log line, no exception, and no stored string."""
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

from engines.base import Credentials, Target
from engines.mvp.browser import open_session
from engines.registry import build_engine

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_CAPABILITY_TEST") != "1", reason="Opt-in real browser test")

SECRET_PASSWORD = "sup3r-secret-hunter2"
WRONG_PASSWORD = "wrong-password-secret"


@pytest.fixture
def login_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            body = b'<div id="signed-in">Welcome</div>' if self.path == "/home" else (
                b'<form action="/login" method="POST">'
                b'<input type="email" name="email"><input type="password" name="password">'
                b'<button type="submit">Login</button></form>'
            )
            self.wfile.write(body)

        def do_POST(self):
            values = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            self.send_response(302)
            self.send_header("Location", "/home" if values.get("password") == [SECRET_PASSWORD] else "/")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    worker.join()
    server.server_close()


def _assert_secret_absent(value, secret=SECRET_PASSWORD) -> None:
    assert secret not in str(value)


async def test_a_failed_login_never_exposes_the_password_in_the_exception(login_server, caplog):
    from engines.mvp.errors import EngineError

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(EngineError) as excinfo:
            await open_session(
                Target(login_server, 1280, 800),
                Credentials({"email": "demo@example.test", "password": WRONG_PASSWORD, "success_selector": "#signed-in"}),
            )
    _assert_secret_absent(excinfo.value, WRONG_PASSWORD)
    _assert_secret_absent(str(excinfo.value.__cause__), WRONG_PASSWORD)
    for record in caplog.records:
        _assert_secret_absent(record.getMessage(), WRONG_PASSWORD)


async def test_a_successful_login_leaves_no_password_in_the_session_or_its_storage_state(login_server, caplog):
    with caplog.at_level(logging.DEBUG):
        session = await open_session(
            Target(login_server, 1280, 800),
            Credentials({"email": "demo@example.test", "password": SECRET_PASSWORD, "success_selector": "#signed-in"}),
        )
    try:
        # Redactor.secrets legitimately holds the raw value in memory — that's
        # how it scrubs other strings. The invariant is that it's never logged
        # or stored, which storage_state (session.state) and caplog cover.
        _assert_secret_absent(session.state)
        for record in caplog.records:
            _assert_secret_absent(record.getMessage())
    finally:
        await session.close()


async def test_full_run_outcome_never_contains_the_password(login_server, caplog):
    from engines.base import CriterionSnapshot, RequirementSnapshot

    class FakeProvider:
        async def structured(self, *, operation, system, user, schema, images=()):
            if operation == "GoalPlan":
                return {
                    "objective": "Check the workspace", "preconditions": [], "expected_behavior": "Workspace is visible",
                    "verification_criteria": ["Workspace visible"], "priority": "medium", "testability": "automatable",
                    "steps": [{"action": "read", "purpose": "inspect", "intent": "read the workspace", "expected_change": "none"}],
                }
            return {"summary": "Workspace observed.", "criteria": [{"criterion_index": 0, "status": "satisfied", "explanation": "Workspace visible.", "evidence_ids": []}]}

    class FakeSink:
        def __init__(self):
            self._next = 0

        async def put_screenshot(self, data, *, step_index=None):
            self._next += 1
            return f"ev-{self._next}"

        async def put_text(self, body, *, kind):
            self._next += 1
            return f"ev-{self._next}"

    requirement = RequirementSnapshot(
        id="req-1", title="Workspace", description="Workspace is visible after login",
        criteria=(CriterionSnapshot(id="c1", text="Workspace is visible"),),
    )
    engine = build_engine("mvp", provider=FakeProvider())
    with caplog.at_level(logging.DEBUG):
        session = await engine.prepare(
            Target(login_server, 1280, 800),
            Credentials({"email": "demo@example.test", "password": SECRET_PASSWORD, "success_selector": "#signed-in"}),
        )
        try:
            outcome = await engine.execute(session, requirement, FakeSink())
        finally:
            await session.close()
    await engine.close()
    _assert_secret_absent(outcome)
    for record in caplog.records:
        _assert_secret_absent(record.getMessage())
