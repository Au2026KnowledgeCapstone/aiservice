"""Step 6 checkpoint: a mid-run redirect to a disallowed origin is aborted,
not silently followed."""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from engines.base import Credentials, Target
from engines.mvp.browser import open_session
from engines.mvp.policy import origin

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_CAPABILITY_TEST") != "1", reason="Opt-in real browser test")


def _start_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker


def _stop_server(server, worker):
    server.shutdown()
    worker.join()
    server.server_close()


@pytest.fixture
def other_origin_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Different origin</body></html>")

    server, worker = _start_server(Handler)
    yield server
    _stop_server(server, worker)


@pytest.fixture
def redirecting_server(other_origin_server):
    other_port = other_origin_server.server_port

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(302)
            # A different port on the same loopback host is still a different
            # origin — exactly the "disallowed origin" case the checkpoint names.
            self.send_header("Location", f"http://127.0.0.1:{other_port}/")
            self.end_headers()

    server, worker = _start_server(Handler)
    yield server
    _stop_server(server, worker)


async def test_a_redirect_to_a_different_origin_is_aborted_not_followed(redirecting_server, other_origin_server):
    target_url = f"http://127.0.0.1:{redirecting_server.server_port}/"
    with pytest.raises(Exception):
        await open_session(Target(target_url, 1280, 800), Credentials({}))


async def test_navigating_the_same_origin_is_unaffected():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Fine</body></html>")

    server, worker = _start_server(Handler)
    try:
        target_url = f"http://127.0.0.1:{server.server_port}/"
        session = await open_session(Target(target_url, 1280, 800), Credentials({}))
        try:
            assert session.policy.target_origin == origin(target_url)
            assert not session.policy.denials
        finally:
            await session.close()
    finally:
        _stop_server(server, worker)
