"""Observe real state changes and bind each screenshot to a consistent page state.

Ported from BlindSpot/backend/app/services/browser/checkpoints.py.
`destination_matches` lives in engines.mvp.urls (needed earlier, by Step 3's
action_contracts.py) rather than being redefined here.
"""
import asyncio
import hashlib
import json
import re
import time
from urllib.parse import urljoin, urlsplit

from engines.mvp.errors import EngineError
from engines.mvp.urls import destination_matches

STATE_JS = r'''() => {
 const visible=el=>el&&el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden'&&!el.closest('[hidden],[inert],[aria-hidden="true"]');
 const headings=[...document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')].filter(visible);
 const regions=[];
 for(const heading of headings.slice(0,40)){
  let node=heading.parentElement;
  for(let depth=0;node&&depth<4;depth++){
   const copy=node.cloneNode(true);copy.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"],button,input,textarea,select,a,script,style').forEach(n=>n.remove());
   if((copy.textContent||'').trim()||node===document.body||node.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]').length>1)break;
   node=node.parentElement;
  }
  if(node){const text=node.innerText||'';const busy=[...node.querySelectorAll('[aria-busy="true"],[role="progressbar"]')].some(visible)||text.split('\n').some(line=>/^(loading\b|please wait\b|fetching\b)/i.test(line.trim()));regions.push({name:(heading.innerText||heading.textContent||'').trim(),text,busy,images:[...node.querySelectorAll('img')].filter(visible).map(image=>({src:image.currentSrc||image.src,loaded:image.complete,width:image.naturalWidth,height:image.naturalHeight}))});}
 }
 const images=[...document.images].filter(visible).map(el=>({src:el.currentSrc||el.src,loaded:el.complete,width:el.naturalWidth,height:el.naturalHeight}));
 return {control_states:[...document.querySelectorAll('button,input,select,textarea,[role="button"],[role="checkbox"],[role="radio"],[role="switch"],[role="option"],[role="tab"]')].filter(visible).slice(0,3000).map(el=>({role:el.getAttribute('role')||el.tagName,name:el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.innerText||'',disabled:!!el.disabled||el.getAttribute('aria-disabled')==='true',checked:el.matches('input[type="checkbox"],input[type="radio"]')?el.checked:el.getAttribute('aria-checked'),selected:el.getAttribute('aria-selected'),expanded:el.getAttribute('aria-expanded')})),visible_controls:[...document.querySelectorAll('button,input,textarea,select,a[href],[role="button"],canvas,svg')].filter(visible).length,popups:[...document.querySelectorAll('dialog[open],[role="dialog"],[role="alertdialog"],[role="menu"],[role="listbox"]')].filter(visible).map(node=>({role:node.getAttribute('role')||'dialog',name:node.getAttribute('aria-label')||node.querySelector('h1,h2,h3,h4')?.innerText||''})),animations_running:(document.getAnimations?.()||[]).some(animation=>animation.playState==='running'&&Number.isFinite(animation.effect?.getComputedTiming().endTime)),url:location.href,text:document.body?.innerText||'',regions,images,busy:[...document.querySelectorAll('[aria-busy="true"],[role="progressbar"]')].some(visible),fonts_ready:!document.fonts||document.fonts.status==='loaded'};
}'''


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def page_state(page):
    raw = await page.evaluate(STATE_JS)
    # Raw state remains transient; only hashes and redacted region descriptions are stored.
    return {
        **raw,
        "visual_hash": digest({"text": raw["text"], "images": raw["images"], "control_states": raw.get("control_states", [])}),
        "state_hash": digest({"url": raw["url"], "text": raw["text"], "images": raw["images"], "control_states": raw.get("control_states", [])}),
        "content_hash": digest({"url": raw["url"], "text": raw["text"], "images": raw["images"], "control_states": raw.get("control_states", [])}),
    }


def change_kind(step):
    if step.action not in {"click", "select", "press", "navigate"}:
        return "none"
    if step.action == "navigate":
        return "url"
    if step.expected_change in {"auto", "none"} and re.search(r"\b(different|switch)\b.*\b(patient|record|entry|account|item)\b", step.intent, re.I):
        return "content"
    if step.expected_change != "auto":
        return step.expected_change
    if re.search(r"\b(different|switch|open|navigate|return|load|refresh|search)\b", step.intent, re.I):
        return "content"
    return "none"


def region_content(state, name):
    if not name:
        return state["content_hash"]
    key = name.strip().casefold().rstrip(":")
    matches = [item for item in state["regions"] if item["name"].strip().casefold().rstrip(":") == key]
    if len(matches) != 1:
        raise EngineError("The expected data region is missing or ambiguous; its update could not be verified.")
    return digest({"text": matches[0]["text"], "images": matches[0].get("images", [])})


def view_changed(before, current, step):
    if current["url"] != before["url"]:
        return True
    old = {item["name"].strip().casefold().rstrip(":") for item in before["regions"]}
    new = {item["name"].strip().casefold().rstrip(":") for item in current["regions"]}
    if step.change_region and step.change_region.strip().casefold().rstrip(":") in new:
        return False
    return bool(old - new and new - old)


def transition_reached(before, current, step):
    kind = change_kind(step)
    if (step.action == "navigate" or step.purpose == "navigate") and step.url:
        return destination_matches(urljoin(before["url"], step.url), current["url"])
    if view_changed(before, current, step):
        # A URL can change before an SPA has replaced the source page's contents.
        return kind != "content" or current["visual_hash"] != before["visual_hash"]
    if kind == "url":
        return current["url"] != before["url"]
    try:
        return region_content(current, step.change_region) != region_content(before, step.change_region)
    except EngineError:
        return False  # A panel can temporarily unmount while a destination is loading.


def destination_content_ready(state):
    # A quiet empty SPA root is not a rendered destination.
    return bool(state.get("text", "").strip() or state.get("visible_controls") or any(image.get("loaded") and image.get("width", 0) > 0 for image in state.get("images", [])))


async def wait_for_transition(page, before, step, timeout=8, quiet=0.8, is_pending=lambda: False):
    kind = change_kind(step)
    if kind == "none":
        return {"required": False, "status": "not_required"}
    baseline = before["url"] if kind == "url" else region_content(before, step.change_region)
    started = time.monotonic()
    last = None
    stable_since = started
    current = before
    while time.monotonic() - started < timeout:
        current = await page_state(page)
        page_changed = view_changed(before, current, step)
        effective_kind = ("url" if current["url"] != before["url"] else "view") if page_changed else kind
        try:
            value = current["content_hash"] if page_changed else current["url"] if kind == "url" else region_content(current, step.change_region)
        except EngineError:
            value = baseline
        changed = transition_reached(before, current, step)
        if value != last:
            last = value
            stable_since = time.monotonic()
        region_busy = any(
            item.get("busy") for item in current["regions"]
            if not page_changed and step.change_region and item["name"].strip().casefold().rstrip(":") == step.change_region.strip().casefold().rstrip(":")
        )
        if (
            changed and destination_content_ready(current) and not current.get("animations_running")
            and not current.get("busy") and not region_busy and time.monotonic() - stable_since >= quiet
            and current["fonts_ready"] and all(image["loaded"] for image in current["images"])
        ):
            return {
                "required": True, "status": "changed" if current["content_hash"] != before["content_hash"] else "destination_reached",
                "destination_verified": bool((step.action == "navigate" or step.purpose == "navigate") and step.url),
                "pending_requests_observed": bool(is_pending()), "kind": effective_kind,
                "page_changed": page_changed, "region": None if page_changed else step.change_region,
                "discarded_source_region": step.change_region if page_changed else None,
                "elapsed_ms": round((time.monotonic() - started) * 1000), "before_hash": before["content_hash"], "after_hash": current["content_hash"],
            }
        await asyncio.sleep(0.1)
    if kind == "url" and (step.action == "navigate" or step.purpose == "navigate") and step.url:
        if transition_reached(before, current, step):
            if not destination_content_ready(current):
                raise EngineError("The destination URL was reached, but the page stayed blank; navigation could not be verified.")
            raise EngineError("The destination was reached, but the visible page did not become ready for verification.")
        raise EngineError("The browser did not reach the planned destination URL before the deadline.")
    raise EngineError("The expected page data did not change before the capture deadline; the resulting state could not be verified.")


def transition_step(step, before):
    """A record switch needs its data panel, not merely a selection indicator."""
    if step.action == "navigate" or step.expected_change == "url":
        return step.model_copy(update={"change_region": None})
    if step.change_region or not re.search(r"\b(different|switch)\b.*\b(patient|record|entry|account|item)\b", step.intent, re.I):
        return step
    words = set(re.findall(r"\w+", step.intent.casefold())) - {
        "the", "a", "an", "for", "to", "in", "from", "of", "and", "is", "on", "with", "different", "switch", "select", "list",
    }
    scored = [
        (len(words & set(re.findall(r"\w+", region["name"].casefold()))), region["name"])
        for region in before["regions"] if len(region["text"]) > len(region["name"]) + 10
    ]
    scored.sort(reverse=True)
    if not scored or scored[0][0] == 0 or len(scored) > 1 and scored[0][0] == scored[1][0]:
        raise EngineError("The changing data panel could not be identified reliably; record selection was not attempted.")
    return step.model_copy(update={"change_region": scored[0][1]})
