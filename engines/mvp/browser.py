"""Session lifecycle: launch, login, per-requirement contexts, observation, close.

Pattern copied from app/capabilities/verification.py for request interception
and WebSocket denial (this codebase's house style for driving a browser).
Also carries the reference's `runtime.py` BrowserSession responsibilities
(observe/resolve_element/wait_ready/capture_is_consistent) — kept in this one
module rather than split into a same-named file, since they're the same live
session object Step 1 already introduced.

No pinned-IP egress proxy here (reference's proxy.py): verification.py, the
pattern this handoff says to copy for driving the browser, does not use one,
and A4 stays consistent with that already-reviewed pattern rather than
building a second, inconsistent mechanism for DNS-pinning/TOCTOU protection.

Request interception is still two-layered, though, because `context.route`
alone measurably is not enough: Playwright's `route()` only sees the first
request of a redirect chain — confirmed directly, not assumed from the
reference's comment about it — so a disallowed-origin redirect would sail
through unchecked with route() alone. The CDP `Fetch` domain (`_attach_cdp_policy`
below) sees every hop and is what actually makes "a mid-run redirect to a
disallowed origin is aborted" true. This is unrelated to the egress-proxy
question above: it needs no raw-socket relay, just a second interception
point in the same page.
"""
import asyncio
from urllib.parse import urlsplit

from engines.base import Credentials, Target
from engines.mvp.checkpoints import page_state
from engines.mvp.contracts import EngineConfiguration
from engines.mvp.discovery import discover
from engines.mvp.errors import EngineError, PolicyDenied
from engines.mvp.policy import NetworkPolicy, origin
from engines.mvp.redaction import Redactor

PREPARE_TIMEOUT_SECONDS = 30
PAGE_DEFAULT_TIMEOUT_MS = 8000
READY_TIMEOUT_MS = 10000
DEFAULT_USERNAME_SELECTOR = 'input[type="email"], input[name="username"]'
DEFAULT_PASSWORD_SELECTOR = 'input[type="password"]'
DEFAULT_SUBMIT_SELECTOR = 'button[type="submit"], input[type="submit"]'

READY_JS = """() => new Promise(resolve => {
  let quiet=Date.now();
  const observer=new MutationObserver(()=>{quiet=Date.now()});
  observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true});
  const started=Date.now();
  const timer=setInterval(()=>{
    const busy=[...document.querySelectorAll('[aria-busy="true"], [role="progressbar"]:not([aria-valuenow])')].some(el=>el.getClientRects().length>0);
    const stable=document.readyState!=='loading' && !!document.body && !busy && Date.now()-quiet>=300;
    if(stable || Date.now()-started>=5000){clearInterval(timer);observer.disconnect();resolve(stable)}
  },100);
})"""


async def _route_handler(policy: NetworkPolicy, route):
    request = route.request
    try:
        await policy.check(request.url, request.method, request.is_navigation_request())
        await route.continue_()
    except PolicyDenied as exc:
        policy.denied(request.url, str(exc), critical=request.is_navigation_request())
        await route.abort()


async def _attach_cdp_policy(context, page, policy: NetworkPolicy) -> None:
    """Catches every redirect hop; route() above only sees the first request."""
    cdp = await context.new_cdp_session(page)

    async def paused(event):
        request = event["request"]
        try:
            await policy.check(request["url"], request["method"], event.get("resourceType") == "Document")
            await cdp.send("Fetch.continueRequest", {"requestId": event["requestId"]})
        except PolicyDenied as exc:
            policy.denied(request["url"], str(exc), critical=event.get("resourceType") in {"Document", "XHR", "Fetch", "Script"})
            await cdp.send("Fetch.failRequest", {"requestId": event["requestId"], "errorReason": "BlockedByClient"})

    cdp.on("Fetch.requestPaused", lambda event: asyncio.ensure_future(paused(event)))
    await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})


async def _login(page, credentials: Credentials) -> bool:
    """Returns True if a login was attempted and verified; False if no credentials were supplied."""
    values = credentials.values
    username = values.get("username") or values.get("email")
    password = values.get("password")
    if not username and not password:
        return False
    if not username or not password:
        raise EngineError("Partial credentials were supplied; both a username and a password are required.")
    success_selector = values.get("success_selector")
    if not success_selector:
        raise EngineError("Login requires a success_selector to confirm authentication.")
    username_selector = values.get("username_selector") or DEFAULT_USERNAME_SELECTOR
    password_selector = values.get("password_selector") or DEFAULT_PASSWORD_SELECTOR
    submit_selector = values.get("submit_selector") or DEFAULT_SUBMIT_SELECTOR
    proof = page.locator(success_selector).first
    # A selector already visible before login is not authentication proof.
    if await proof.is_visible():
        raise EngineError("The login success condition was already visible before authentication.")
    try:
        await page.locator(username_selector).first.fill(username)
        await page.locator(password_selector).first.fill(password)
        await page.locator(submit_selector).first.click()
        await proof.wait_for(state="visible")
    except Exception as exc:
        # Browser exceptions can contain form values or URLs; never store/log them.
        raise EngineError("Login could not be verified against the configured success condition.") from exc
    return True


class MvpSession:
    """Holds the live browser for one run. Credentials never touch this object."""

    def __init__(self, playwright, browser, policy: NetworkPolicy, redactor: Redactor, target: Target, state=None):
        self.playwright = playwright
        self.browser = browser
        self.policy = policy
        self.redactor = redactor
        self.target_url = target.url
        self.viewport = {"width": target.viewport_width, "height": target.viewport_height}
        self.configuration = EngineConfiguration()
        self.state = state
        self.elements = {}
        self.observation_sequence = 0
        self.discovery_hint = ""
        self.recording_started_at = None
        self.auth_evidence = {"method": "credentials" if state else "none", "login_completed": bool(state), "isolated_session_verified": False}

    async def open_context(self):
        """A fresh context per requirement, carrying login forward via storage_state."""
        kwargs = {"service_workers": "block", "accept_downloads": False, "viewport": self.viewport}
        if self.state:
            kwargs["storage_state"] = self.state
        context = await self.browser.new_context(**kwargs)
        context.set_default_timeout(PAGE_DEFAULT_TIMEOUT_MS)

        async def route_handler(route):
            await _route_handler(self.policy, route)

        await context.route("**/*", route_handler)
        await context.route_web_socket("**/*", lambda ws: ws.close())
        page = await context.new_page()
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))
        await _attach_cdp_policy(context, page, self.policy)
        return context, page

    async def open_landing(self, page) -> None:
        await self.policy.check(self.target_url, "GET", True)
        response = await page.goto(self.target_url, wait_until="domcontentloaded")
        if response and response.status >= 400:
            raise EngineError("Target returned an HTTP error. Check the application URL and base path.")
        await self.wait_ready(page)
        if self.state is not None:
            self.auth_evidence["isolated_session_verified"] = True

    async def wait_ready(self, page) -> None:
        await page.wait_for_load_state("domcontentloaded", timeout=READY_TIMEOUT_MS)
        # Fixed trusted script: DOM must be quiet and visible busy indicators absent.
        ready = await page.evaluate(READY_JS)
        if not ready:
            raise EngineError("Page did not become ready: loading indicators or continuing DOM updates prevented a stable checkpoint.")

    async def observe(self, page) -> dict:
        if origin(page.url) != self.policy.target_origin:
            raise PolicyDenied("Observation outside target origin blocked")
        self.observation_sequence += 1
        observation_id = f"o{self.observation_sequence}"
        self.elements = {}
        snapshot = await page.locator("body").aria_snapshot(timeout=4000)
        controls, self.elements, discovery = await discover(page, observation_id, self.redactor, self.discovery_hint)
        content_images = []
        images = page.locator("img")
        for index in range(min(await images.count(), 20)):
            element = images.nth(index)
            if not await element.is_visible():
                continue
            dimensions = await element.evaluate("el => ({loaded: el.complete && el.naturalWidth > 0, width: el.naturalWidth, height: el.naturalHeight})")
            content_images.append({"alt": self.redactor.text(await element.get_attribute("alt") or "", 300), **dimensions})
        state = await page_state(page)
        regions = [{"name": self.redactor.text(item["name"], 200), "text": self.redactor.text(item["text"], 300)} for item in state["regions"][:20]]
        return {
            "observation_id": observation_id,
            "url": self.redactor.text(origin(page.url) + urlsplit(page.url).path, 2000),
            "accessible_ui": self.redactor.text(snapshot, 12000),
            "controls": controls,
            "content_images": content_images,
            "discovery": discovery,
            "content_regions": regions,
            "interaction_context": {"popups": [{"role": popup["role"], "name": self.redactor.text(popup["name"], 200)} for popup in state["popups"]]},
            "page_state_hash": state["state_hash"],
            "visual_content_hash": state["visual_hash"],
            "render_ready": state["fonts_ready"] and not state["animations_running"] and all(image["loaded"] for image in state["images"]),
        }

    async def capture_is_consistent(self, page, observation) -> bool:
        state = await page_state(page)
        snapshot = await page.locator("body").aria_snapshot(timeout=4000)
        return (
            observation.get("render_ready", False)
            and not state["animations_running"]
            and state["state_hash"] == observation["page_state_hash"]
            and self.redactor.text(snapshot, 12000) == observation["accessible_ui"]
            and state["fonts_ready"]
            and all(image["loaded"] for image in state["images"])
        )

    async def resolve_element(self, element_id):
        candidate = self.elements.get(element_id)
        if candidate is None:
            raise EngineError("Selected element ID is not in the current observation.")
        if await candidate.count() != 1 or not await candidate.is_visible():
            raise EngineError("Observed control changed or became ambiguous before interaction.")
        return candidate

    async def close(self) -> None:
        try:
            await asyncio.wait_for(self.browser.close(), 10)
        except Exception:
            pass
        finally:
            try:
                await asyncio.wait_for(self.playwright.stop(), 5)
            except Exception:
                pass


async def open_session(target: Target, credentials: Credentials) -> MvpSession:
    from playwright.async_api import async_playwright

    policy = NetworkPolicy(target.url)
    redactor = Redactor(credentials.values)
    async with asyncio.timeout(PREPARE_TIMEOUT_SECONDS):
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch(headless=True, args=["--no-proxy-server"])
            try:
                state = None
                context = await browser.new_context(
                    service_workers="block", accept_downloads=False,
                    viewport={"width": target.viewport_width, "height": target.viewport_height},
                )
                context.set_default_timeout(PAGE_DEFAULT_TIMEOUT_MS)
                try:

                    async def route_handler(route):
                        await _route_handler(policy, route)

                    await context.route("**/*", route_handler)
                    await context.route_web_socket("**/*", lambda ws: ws.close())
                    page = await context.new_page()
                    await _attach_cdp_policy(context, page, policy)
                    await policy.check(target.url, "GET", True)
                    await page.goto(target.url, wait_until="domcontentloaded")
                    if await _login(page, credentials):
                        state = await context.storage_state()
                finally:
                    await context.close()
            except BaseException:
                await browser.close()
                raise
        except BaseException:
            await playwright.stop()
            raise
    return MvpSession(playwright, browser, policy, redactor, target, state=state)
