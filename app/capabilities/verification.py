"""Deterministic login proof; credentials live only in this request's memory."""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit
from app.capabilities.errors import CapabilityError
from app.capabilities.schemas import VerificationResponse


async def target(url, config, approved=None):
    try:
        parsed = urlsplit(url)
        origin = (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    except ValueError:
        raise CapabilityError("Blocked target URL.", 422) from None
    if url != url.strip() or any(ord(c) <= 32 for c in url) or parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise CapabilityError("Blocked target URL.", 422)
    if approved is not None and origin != approved:
        raise CapabilityError("Cross-origin navigation is blocked.", 422)
    hostname = parsed.hostname.lower().rstrip('.')
    if hostname in {'metadata.google.internal', '169.254.169.254'}:
        raise CapabilityError("Metadata endpoints are blocked.", 422)
    try:
        addresses = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(hostname, origin[2], type=socket.SOCK_STREAM), 5)
    except (OSError, TimeoutError):
        raise CapabilityError("Target DNS resolution failed.", 422) from None
    if any(ipaddress.ip_address(item[4][0]).is_link_local or item[4][0] in {'169.254.170.2', 'fd00:ec2::254'} for item in addresses):
        raise CapabilityError("Metadata/link-local target is blocked.", 422)
    allowed = {h.strip().lower() for h in config.verification_allowed_private_hosts.split(',') if h.strip()}
    if not addresses or (hostname not in allowed and any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses)):
        raise CapabilityError("Private or reserved network target is blocked.", 422)
    return origin


class BrowserVerifier:
    def __init__(self, config):
        self.config = config

    def configured(self):
        if not self.config.verification_enabled:
            raise CapabilityError("Browser verification is disabled.", 503)
        try:
            import playwright.async_api
        except ImportError:
            raise CapabilityError("Install the engine extra and Chromium to enable verification.", 503) from None

    async def verify(self, request):
        self.configured()
        values = request.credentials
        def response(status, reason):
            return VerificationResponse(request_id=request.request_id, environment_id=request.environment_id,
                revision=request.revision, status=status, reason=reason)
        username = values.get('username') or values.get('email')
        password = values.get('password')
        success = values.get('success_selector')
        if not username or not password or not success:
            return response('inconclusive', 'success_condition_missing')
        try:
            approved = await target(request.target_url, self.config)
        except CapabilityError:
            return response('inconclusive', 'target_blocked')
        from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
        try:
            async with asyncio.timeout(self.config.verification_timeout_seconds):
                async with async_playwright() as playwright:
                    options = {'headless': True, 'args': ['--no-proxy-server']}
                    if self.config.verification_browser_executable:
                        options['executable_path'] = self.config.verification_browser_executable
                    browser = await playwright.chromium.launch(**options)
                    try:
                        context = await browser.new_context(service_workers='block', accept_downloads=False)
                        async def intercept(route):
                            try:
                                await target(route.request.url, self.config, approved)
                                await route.continue_()
                            except CapabilityError:
                                await route.abort()
                        await context.route('**/*', intercept)
                        async def deny_socket(websocket):
                            await websocket.close()
                        await context.route_web_socket('**/*', deny_socket)
                        page = await context.new_page()
                        page.set_default_timeout(min(10000, self.config.verification_timeout_seconds * 1000))
                        await page.goto(request.target_url, wait_until='domcontentloaded')
                        proof = page.locator(success).first
                        # A selector already visible before login is not authentication proof.
                        if await proof.is_visible():
                            return response('inconclusive', 'success_condition_missing')
                        await page.locator(values.get('username_selector', 'input[type="email"], input[name="username"]')).first.fill(username)
                        await page.locator(values.get('password_selector', 'input[type="password"]')).first.fill(password)
                        await page.locator(values.get('submit_selector', 'button[type="submit"], input[type="submit"]')).first.click()
                        await proof.wait_for(state='visible')
                        await target(page.url, self.config, approved)
                        return response('verified', 'success_observed')
                    finally:
                        await browser.close()
        except (PlaywrightTimeout, TimeoutError):
            return response('rejected', 'login_failed')
        except Exception:
            # Browser errors can contain form values or URLs; never store/log them.
            return response('inconclusive', 'browser_error')
