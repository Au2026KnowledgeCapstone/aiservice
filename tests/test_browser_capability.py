"""Opt-in real Chromium against a local synthetic login form; no outside target."""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from uuid import uuid4
import pytest
from app.capabilities.verification import BrowserVerifier, target
from app.capabilities.schemas import VerificationRequest
from app.capabilities.errors import CapabilityError
from tests.test_intake import config


@pytest.fixture
def login_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<div id="signed-in">Welcome</div>' if self.path == '/home' else
                b'<form action="/login" method="POST"><input type="email" name="email"><input type="password" name="password"><button type="submit">Login</button></form>')
        def do_POST(self):
            values = parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
            self.send_response(302)
            self.send_header('Location', '/home' if values.get('password') == ['correct'] else '/')
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    worker.join()
    server.server_close()


@pytest.mark.skipif(os.getenv('RUN_BROWSER_CAPABILITY_TEST') != '1', reason='Opt-in real browser test')
@pytest.mark.parametrize('password,status', [('correct', 'verified'), ('wrong', 'rejected')])
async def test_real_login_success_and_failure(login_server, password, status):
    settings = config(verification_enabled=True, verification_allowed_private_hosts='127.0.0.1', verification_timeout_seconds=5)
    request = VerificationRequest(request_id=str(uuid4()), environment_id=str(uuid4()), revision=1, target_url=login_server,
        credentials={'email': 'demo@example.test', 'password': password, 'success_selector': '#signed-in'})
    response = await BrowserVerifier(settings).verify(request)
    assert response.status == status, response


async def test_metadata_and_unapproved_private_targets_are_blocked():
    with pytest.raises(CapabilityError): await target('http://169.254.169.254', config())
    with pytest.raises(CapabilityError): await target('http://127.0.0.1', config())
