"""Session lifecycle: launch, request interception, login, close.

Pattern copied from app/capabilities/verification.py (this codebase's house
style for driving a browser) — reimplemented here because engines/ must not
import app/.
"""
import asyncio

from engines.base import Credentials, Target
from engines.mvp.errors import EngineError, PolicyDenied
from engines.mvp.policy import NetworkPolicy
from engines.mvp.redaction import Redactor

PREPARE_TIMEOUT_SECONDS = 30
DEFAULT_USERNAME_SELECTOR = 'input[type="email"], input[name="username"]'
DEFAULT_PASSWORD_SELECTOR = 'input[type="password"]'
DEFAULT_SUBMIT_SELECTOR = 'button[type="submit"], input[type="submit"]'


class MvpSession:
    """Holds the live Playwright context for one run. Credentials never touch this object."""

    def __init__(self, playwright, browser, context, page, policy: NetworkPolicy, redactor: Redactor, target_url: str):
        self.playwright = playwright
        self.browser = browser
        self.context = context
        self.page = page
        self.policy = policy
        self.redactor = redactor
        self.target_url = target_url

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


async def _route_handler(policy: NetworkPolicy, route):
    request = route.request
    try:
        await policy.check(request.url, request.method, request.is_navigation_request())
        await route.continue_()
    except PolicyDenied as exc:
        policy.denied(request.url, str(exc), critical=request.is_navigation_request())
        await route.abort()


async def _login(page, credentials: Credentials, redactor: Redactor) -> bool:
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


async def open_session(target: Target, credentials: Credentials) -> MvpSession:
    from playwright.async_api import async_playwright

    policy = NetworkPolicy(target.url)
    redactor = Redactor(credentials.values)
    async with asyncio.timeout(PREPARE_TIMEOUT_SECONDS):
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch(headless=True, args=["--no-proxy-server"])
            try:
                context = await browser.new_context(
                    service_workers="block",
                    accept_downloads=False,
                    viewport={"width": target.viewport_width, "height": target.viewport_height},
                )

                async def route_handler(route):
                    await _route_handler(policy, route)

                await context.route("**/*", route_handler)
                await context.route_web_socket("**/*", lambda ws: ws.close())
                page = await context.new_page()
                await policy.check(target.url, "GET", True)
                await page.goto(target.url, wait_until="domcontentloaded")
                await _login(page, credentials, redactor)
            except BaseException:
                await browser.close()
                raise
        except BaseException:
            await playwright.stop()
            raise
    return MvpSession(playwright, browser, context, page, policy, redactor, target.url)
