"""Step 1 checkpoint: ENGINE=mvp builds, a session opens/closes against a local page."""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from engines.base import Credentials, Target
from engines.mvp.browser import MvpSession
from engines.mvp.policy import origin, safe_url
from engines.registry import build_engine


class _FakeProvider:
    async def structured(self, **kwargs):
        raise NotImplementedError


@pytest.fixture
def local_page():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Local fixture page</h1></body></html>")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    worker.join()
    server.server_close()


def test_build_mvp_constructs_without_a_browser():
    engine = build_engine("mvp", provider=_FakeProvider())
    assert engine.name == "mvp"


def test_origin_and_safe_url_are_pure():
    assert origin("https://example.test/path") == "https://example.test"
    assert origin("http://example.test:8080/x") == "http://example.test:8080"
    assert safe_url("not a url") == "[blocked URL]"


@pytest.mark.skipif(os.getenv("RUN_BROWSER_CAPABILITY_TEST") != "1", reason="Opt-in real browser test")
async def test_prepare_opens_and_close_tears_down_cleanly(local_page):
    engine = build_engine("mvp", provider=_FakeProvider())
    session = await engine.prepare(Target(local_page, 1280, 800), Credentials({}))
    try:
        assert isinstance(session, MvpSession)
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            assert "Local fixture page" in await page.content()
        finally:
            await context.close()
    finally:
        await session.close()
    await engine.close()


@pytest.mark.skipif(os.getenv("RUN_BROWSER_CAPABILITY_TEST") != "1", reason="Opt-in real browser test")
async def test_prepare_rejects_private_target():
    from engines.mvp.errors import PolicyDenied

    engine = build_engine("mvp", provider=_FakeProvider())
    with pytest.raises(PolicyDenied):
        await engine.prepare(Target("http://169.254.169.254/", 1280, 800), Credentials({}))
