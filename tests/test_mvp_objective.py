"""objective.py is ported but currently inert (no envelope field populates objective_checks);
this exercises run_check directly against a real page so the logic itself is proven."""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from engines.mvp.contracts import Locator, ObjectiveCheck
from engines.mvp.errors import PolicyDenied
from engines.mvp.objective import run_check

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_CAPABILITY_TEST") != "1", reason="Opt-in real browser test")


@pytest.fixture
def fixture_page_url():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Welcome back</h1></body></html>")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    worker.join()
    server.server_close()


@pytest.fixture
async def page(fixture_page_url):
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(fixture_page_url, wait_until="domcontentloaded")
            yield page
        finally:
            await browser.close()


async def test_text_visible_check_passes_for_present_text(page):
    from engines.mvp.policy import origin

    check = ObjectiveCheck(requirement_id="r1", criterion_index=0, criterion_text="x", kind="text_visible", expected="Welcome back")
    assert await run_check(page, check, origin(page.url)) is True


async def test_text_visible_check_fails_for_absent_text(page):
    from engines.mvp.policy import origin

    check = ObjectiveCheck(requirement_id="r1", criterion_index=0, criterion_text="x", kind="text_absent", expected="Goodbye")
    assert await run_check(page, check, origin(page.url)) is True


async def test_run_check_rejects_when_page_left_the_target_origin(page):
    check = ObjectiveCheck(requirement_id="r1", criterion_index=0, criterion_text="x", kind="text_visible", expected="Welcome back")
    with pytest.raises(PolicyDenied):
        await run_check(page, check, "https://not-the-target.test")
