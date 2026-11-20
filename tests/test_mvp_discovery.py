"""Step 2 checkpoint: ambiguity disables controls, credential fields are omitted, no CSS selectors leak."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from engines.mvp.discovery import discover
from engines.mvp.page_summary import describe_page
from engines.mvp.redaction import Redactor

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_CAPABILITY_TEST") != "1", reason="Opt-in real browser test")

FIXTURE_HTML = b"""
<html><body>
  <button>Submit</button>
  <button>Submit</button>
  <button>Cancel</button>
  <form>
    <input type="password" name="password">
    <input type="email" name="email">
    <input type="text" name="comment" placeholder="Leave a comment">
  </form>
</body></html>
"""


@pytest.fixture
def fixture_page_url():
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


async def test_duplicate_buttons_are_ambiguous_and_unavailable(page):
    controls, bindings, _ = await discover(page, "obs-1", Redactor({}))
    submits = [c for c in controls if c["name"] == "Submit"]
    assert len(submits) == 2
    assert all(c["ambiguous"] for c in submits)
    assert all(not c["actionable"] for c in submits)
    assert all(c["element_id"] not in bindings for c in submits)


async def test_distinct_button_stays_actionable_and_bound(page):
    controls, bindings, _ = await discover(page, "obs-1", Redactor({}))
    cancel = next(c for c in controls if c["name"] == "Cancel")
    assert cancel["actionable"] is True
    assert not cancel["ambiguous"]
    assert cancel["element_id"] in bindings


async def test_password_and_email_fields_are_excluded_from_inventory(page):
    controls, _, _ = await discover(page, "obs-1", Redactor({}))
    names_and_placeholders = [c.get("name", "") for c in controls]
    assert not any("password" in str(v).lower() for v in names_and_placeholders)
    # Only the plain text comment field should appear among the inputs.
    text_inputs = [c for c in controls if c.get("element") == "input"]
    assert len(text_inputs) == 1
    assert text_inputs[0]["placeholder"] == "Leave a comment"


async def test_no_control_exposes_a_css_selector(page):
    controls, _, _ = await discover(page, "obs-1", Redactor({}))
    serialized = json.dumps(controls)
    for forbidden in ("querySelector", "#", ".btn", "nth-child", "css="):
        assert forbidden not in serialized


async def test_elements_are_identified_by_element_id_not_selector(page):
    controls, bindings, _ = await discover(page, "obs-1", Redactor({}))
    for control in controls:
        assert control["element_id"].startswith("obs-1-e")
        assert "selector" not in control


def test_describe_page_summarizes_without_inventory_dump():
    snapshot = '- heading "Welcome"\n- button "Save"\n'
    assert describe_page(snapshot) == 'The page showed “Welcome”, with “Save” controls visible.'


def test_describe_page_handles_non_snapshot_text():
    assert describe_page("plain text body") == "plain text body"


# --- Repeated controls: redundant copies vs. genuine ambiguity -----------------

CAROUSEL_HTML = b"""
<html><body>
  <div id="products">
    <a href="/products/p1">Mouse 46 reviews $350</a>
    <a href="/products/p7">Keyboard 20 reviews $120</a>
  </div>
  <div id="more-products">
    <!-- The same product linked a second time, as a carousel does. -->
    <a href="/products/p1">Mouse 46 reviews $350</a>
    <!-- Same label, different product: a real fork, not a copy. -->
    <a href="/products/p9">Keyboard 20 reviews $120</a>
  </div>
</body></html>
"""


@pytest.fixture
def carousel_page_url():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(CAROUSEL_HTML)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    worker.join()
    server.server_close()


@pytest.fixture
async def carousel_page(carousel_page_url):
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(carousel_page_url, wait_until="domcontentloaded")
            yield page
        finally:
            await browser.close()


async def test_repeated_link_to_one_destination_keeps_one_actionable_copy(carousel_page):
    """Regression: Amazon-Clone lists the same product in two carousels. Both
    copies used to be flagged ambiguous, which withdrew the product entirely and
    failed the run. Clicking either goes to the same URL, so one stays usable."""
    controls, bindings, _ = await discover(carousel_page, "obs-1", Redactor({}))
    mice = [c for c in controls if c["name"].startswith("Mouse")]

    assert len(mice) == 2, "the fixture must render the product twice"
    assert not any(c["ambiguous"] for c in mice), "copies of one destination are redundant, not ambiguous"
    actionable = [c for c in mice if c["actionable"]]
    assert len(actionable) == 1
    assert actionable[0]["element_id"] in bindings


async def test_the_surviving_copy_still_validates_with_its_twin_on_the_page(carousel_page):
    """The frozen handle has to survive its own duplicate: this is the check
    (discovery.py's VALIDATE) that produced "Observed control changed or became
    ambiguous before interaction" in production."""
    controls, bindings, _ = await discover(carousel_page, "obs-1", Redactor({}))
    mouse = next(c for c in controls if c["name"].startswith("Mouse") and c["actionable"])

    assert await bindings[mouse["element_id"]].count() == 1


async def test_repeated_name_with_different_destinations_stays_ambiguous(carousel_page):
    """The relaxation must not leak: same label, different product, still refused."""
    controls, bindings, _ = await discover(carousel_page, "obs-1", Redactor({}))
    keyboards = [c for c in controls if c["name"].startswith("Keyboard")]

    assert len(keyboards) == 2
    assert all(c["ambiguous"] for c in keyboards)
    assert all(not c["actionable"] for c in keyboards)
    assert all(c["element_id"] not in bindings for c in keyboards)
