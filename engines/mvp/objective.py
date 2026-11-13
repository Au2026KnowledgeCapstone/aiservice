"""User-configured objective checks; AI cannot create or broaden these rules.

Ported from BlindSpot/backend/app/services/browser/objective.py. Functionally
inert in A4: `EngineConfiguration.objective_checks` is always empty (there is
no envelope field to populate it from — see contracts.py), so `run_check`
currently has no caller with real checks to run. Ported now so the Step 4
executor can call it uniformly, and so a future envelope extension has a
working implementation to plug into rather than a stub.
"""
from urllib.parse import urlsplit

from playwright.async_api import expect

from engines.mvp.contracts import locate
from engines.mvp.errors import EngineError, PolicyDenied
from engines.mvp.policy import origin


async def run_check(page, check, target_origin):
    if origin(page.url) != target_origin:
        raise PolicyDenied("Objective check left the approved target origin.")
    target = locate(page, check.target) if check.target else None
    if check.kind == "url_path":
        try:
            await page.wait_for_url(
                lambda url: origin(str(url)) == target_origin and urlsplit(str(url)).path == check.expected,
                timeout=5000,
            )
        except Exception as exc:
            if type(exc).__name__ != "TimeoutError":
                raise
            return False
        return True
    if check.kind in {"text_visible", "text_absent"}:
        target = target or page.get_by_text(check.expected, exact=True)
        if check.target:
            target = target.get_by_text(check.expected, exact=True)
    try:
        if check.kind == "text_visible":
            await expect(target).to_be_visible(timeout=5000)
        elif check.kind == "text_absent":
            await expect(target).to_have_count(0, timeout=5000)
        elif check.kind == "count":
            await expect(target).to_have_count(int(check.expected), timeout=5000)
        else:
            # Missing/ambiguous controls do not prove an enabled/value discrepancy.
            await expect(target).to_have_count(1, timeout=5000)
            await expect(target).to_be_visible(timeout=5000)
            if check.kind == "enabled":
                await expect(target).to_be_enabled(timeout=5000)
            elif check.kind == "disabled":
                await expect(target).to_be_disabled(timeout=5000)
            elif check.kind == "value":
                await expect(target).to_have_value(check.expected, timeout=5000)
        return True
    except AssertionError:
        if check.kind in {"enabled", "disabled", "value"} and (await target.count() != 1 or not await target.is_visible()):
            raise EngineError("Objective check target was missing or ambiguous; criterion remains inconclusive.") from None
        return False
