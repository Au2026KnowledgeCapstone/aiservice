"""Compile semantic goals against observed controls; never execute an unchecked proposal.

Ported from BlindSpot/backend/app/services/browser/action_contracts.py.
Imports are the local engines.mvp equivalents (engines/ cannot import app.*);
the compilation and verification logic itself is unchanged. `verify_action`
calls `session.resolve_element` / `session.capture_is_consistent`, which land
on `MvpSession` in Step 4 — this module only needs those methods to exist on
whatever `session` object it is handed, not to import them.
"""
from dataclasses import dataclass
import re
from urllib.parse import urljoin, urlsplit

from engines.mvp.errors import ActionContractError
from engines.mvp.form_entries import is_form_entry, matched_entry
from engines.mvp.policy import origin
from engines.mvp.repeatability import control_identity
from engines.mvp.urls import destination_matches


@dataclass
class BoundAction:
    step: object
    purpose: str
    postcondition: str
    expected: object = None
    identity: dict | None = None
    proposal: str = ""
    corrected: bool = False
    source_observation_id: str | None = None
    proposed_key: str | None = None

    def record(self):
        return {
            "status": "validated", "purpose": self.purpose, "proposed_action": self.proposal,
            "proposed_key": self.proposed_key, "action": self.step.action,
            "corrected_before_execution": self.corrected, "source_observation_id": self.source_observation_id,
            "postcondition": self.postcondition,
            "expected_destination": self.expected if self.postcondition == "destination_url" else None,
        }


def words(value):
    return set(re.findall(r"[^\W_]+", str(value).casefold()))


def norm(value):
    return " ".join(re.findall(r"[^\W_]+", str(value).casefold()))


def checked(value):
    return value if isinstance(value, bool) else {"true": True, "false": False}.get(str(value).lower())


def known_destination(step, control, observations, requirement, configuration, target_url):
    known = {item["url"] for item in observations if item.get("url")}
    known.add(target_url)
    for item in observations:
        known.update(control["href"] for control in item.get("controls", []) if control.get("href"))
    for check in configuration.objective_checks:
        if check.requirement_id == requirement.get("id") and check.kind == "url_path":
            known.add(urljoin(target_url, check.expected))
    documented = " ".join(str(requirement.get(key, "")) for key in ("title", "description", "acceptance_criteria", "source"))
    known.update(
        urljoin(target_url, match.rstrip(".,;)"))
        for match in re.findall(r"https?://[^\s\"'<>]+|(?<![\w/])/[\w/.-]+", documented)
    )
    if control and control.get("href"):
        return urljoin(target_url, control["href"])
    if step.url:
        proposed = urljoin(target_url, step.url)
        if proposed in known:
            return proposed
    query = words(step.destination_label or step.intent) - words(
        "the a an to from for of in on and current selected patient return navigate go page view"
    )
    scores = {}
    for observation in observations:
        url = observation.get("url")
        if not url:
            continue
        labels = [region["name"] for region in observation.get("content_regions", [])]
        labels.append(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
        score = max((len(words(label) & query) for label in labels), default=0)
        scores[url] = max(score, scores.get(url, 0))
    best = max(scores.values(), default=0)
    matches = [url for url, score in scores.items() if best and score == best]
    if len(matches) > 1:
        raise ActionContractError("More than one observed destination matches this goal; no destination was guessed.")
    return matches[0] if len(matches) == 1 else None


def observed_navigation_control(step, observation, history, requirement, configuration, target_url):
    """Prefer one freshly observed route control over a document reload; never guess."""
    expected = known_destination(step, None, history, requirement, configuration, target_url)
    if not expected:
        return None
    controls = [
        item for item in observation.get("controls", [])
        if item.get("actionable", True) and not item.get("disabled") and not item.get("blocked_by_modal")
        and item.get("role") in {"link", "button", "menuitem", "tab"}
    ]
    exact = [item for item in controls if item.get("href") and destination_matches(expected, urljoin(observation["url"], item["href"]))]
    if len(exact) > 1:
        raise ActionContractError("Multiple observed navigation controls lead to the destination; no control was guessed.")
    if exact:
        return exact[0]
    # Back Home is appropriate for a documented Dashboard/Home destination, not arbitrary back navigation.
    destination = norm(step.destination_label or "")
    path = norm(urlsplit(expected).path.rstrip("/").rsplit("/", 1)[-1])
    home = destination in {"dashboard", "home"} or path in {"dashboard", "home"}
    candidates = [
        item for item in controls
        if home and norm(item.get("name", "")) in {"back home", "back to home", "back to dashboard", "dashboard", "home", "return to dashboard", "return home"}
        and not item.get("href") and not (item.get("has_form") and item.get("input_type") == "submit")
    ]
    if len(candidates) > 1:
        raise ActionContractError("Multiple observed controls match the return destination; no control was guessed.")
    return candidates[0] if candidates else None


def compile_action(step, control, observation, history, requirement, configuration, target_url, index):
    action = step.action
    role = (control or {}).get("role") or ((control or {}).get("locator") or {}).get("role")
    purpose = step.purpose
    if purpose == "auto":
        purpose = "navigate" if action == "navigate" or step.expected_change == "url" or role == "link" else {
            "fill": "enter_text", "select": "select_option", "check": "set_checked", "read": "inspect",
            "observe": "inspect", "screenshot": "inspect", "wait": "inspect", "press": "keyboard",
        }.get(action, "activate")
    if purpose == "activate" and action == "click" and role in {"checkbox", "radio", "switch"}:
        purpose = "set_checked"
    if purpose == "activate" and action == "click" and role in {"textbox", "searchbox", "spinbutton"}:
        purpose = "focus"
    if action in {"click", "fill", "select", "check", "press", "wait"}:
        if not control or not control.get("actionable", True) or control.get("disabled") or control.get("blocked_by_modal"):
            raise ActionContractError("The action has no available, unambiguous control on the current page.")
    identity = control_identity(control) if control else None
    post = "observation_recorded"
    expected = None
    updates = {}
    if purpose == "navigate":
        entry = matched_entry(control, observation)
        if is_form_entry(control) and not entry:
            raise ActionContractError("Opening this creation flow needs a matching observed discovery transition; no action was taken.")
        expected = entry["destination_url"] if entry else known_destination(step, control, history, requirement, configuration, target_url)
        if control:
            if role not in {"button", "link", "menuitem", "tab"}:
                raise ActionContractError("Navigation requires an observed navigation button or link.")
            label = control.get("name", "")
            if re.search(r"\b(delete|remove|save|submit|pay|send|purchase)\b", label, re.I) or not entry and role != "link" and not re.search(
                r"\b(back|home|dashboard|return|open|view|go|next|previous|exit|logout|sign out|log out|message|report|reports|profile|settings|confirm selection)\b", label, re.I
            ):
                raise ActionContractError("This control cannot be established as navigation; no action was taken.")
            if control.get("has_form") and control.get("input_type") == "submit":
                raise ActionContractError("A submit button cannot be used as a navigation shortcut.")
            updates = {
                "action": "click", "value": None,
                "expected_change": "content" if entry and entry["effect"] == "content_update" else "url" if expected else "content",
                "change_region": None, "url": expected,
            }
        else:
            if action != "navigate" or not expected:
                raise ActionContractError("Navigation needs an observed control or a grounded destination URL.")
            updates = {"action": "navigate", "url": expected, "expected_change": "url", "change_region": None}
        if expected and origin(expected) != origin(target_url):
            raise ActionContractError("The navigation destination is outside the approved application origin.")
        post = "destination_observed" if entry and entry["effect"] == "content_update" else "destination_url" if expected else "destination_observed"
    elif purpose == "focus":
        if not control:
            raise ActionContractError("Focus requires an observed focusable control.")
        updates = {"action": "click", "expected_change": "none"}
        post = "control_focused"
    elif purpose == "enter_text":
        if role not in {"textbox", "searchbox", "spinbutton"} or not control.get("editable") or control.get("readonly") or step.value is None:
            raise ActionContractError("Text entry requires an editable text field and a planned value.")
        updates = {"action": "fill"}
        post = "value_matches"
        expected = step.value
    elif purpose == "select_option":
        if control.get("element") != "select" or step.value is None:
            raise ActionContractError("Option selection requires an observed native select control and a planned option.")
        options = [option["label"] for option in control.get("options", []) if option["label"].casefold() == step.value.casefold()]
        if len(options) != 1:
            raise ActionContractError("The requested option is missing or ambiguous in the observed select control.")
        updates = {"action": "select", "value": options[0]}
        post = "option_matches"
        expected = options[0]
    elif purpose == "set_checked":
        if role not in {"checkbox", "radio", "switch"}:
            raise ActionContractError("A checked-state action requires an observed checkbox, radio button or switch.")
        if step.checked is None and action != "check":
            raise ActionContractError("Specify whether the toggle must be checked or unchecked before interacting.")
        expected = step.checked if step.checked is not None else True
        if role == "radio" and expected is False:
            raise ActionContractError("Select another radio option instead of unchecking a radio button.")
        if control.get("element") == "input":
            updates = {"action": "check", "checked": expected}
        else:
            current = checked(control.get("checked"))
            if current is None:
                raise ActionContractError("The toggle state is unavailable; its desired state cannot be set safely.")
            updates = {"action": "click" if current != expected else "observe", "checked": expected, "expected_change": "none"}
        post = "checked_matches"
    elif purpose == "dismiss":
        if not control:
            raise ActionContractError("Dismissal needs an observed popup control.")
        if not control.get("in_popup") and control.get("expanded") != "true":
            raise ActionContractError("There is no observed popup associated with this control to dismiss.")
        if role == "button" and re.search(r"\b(close|cancel|dismiss)\b", control.get("name", ""), re.I):
            updates = {"action": "click", "expected_change": "none"}
        else:
            updates = {"action": "press", "value": "Escape", "expected_change": "none"}
        if len(observation.get("interaction_context", {}).get("popups", [])) > 1:
            raise ActionContractError("More than one popup is visible; the dismissal target is ambiguous.")
        post = "popup_closed"
        expected = {"owner_expanded": control.get("expanded") == "true", "popup_count": len(observation.get("interaction_context", {}).get("popups", []))}
    elif purpose == "submit":
        if not control.get("has_form"):
            raise ActionContractError("Submission requires an observed field or submit button associated with a form.")
        if role == "button" and control.get("input_type") == "submit":
            updates = {"action": "click", "value": None}
        elif role in {"textbox", "searchbox", "spinbutton"}:
            updates = {"action": "press", "value": "Enter"}
        else:
            raise ActionContractError("The observed control cannot submit this form.")
        post = "content_changed"
        if step.expected_change in {"auto", "none"}:
            updates["expected_change"] = "content"
    elif purpose == "keyboard":
        source = " ".join(str(requirement.get(key, "")) for key in ("title", "description", "acceptance_criteria", "source"))
        key = step.value
        if action != "press" or not (key and (re.search(r"\b" + re.escape(key) + r"\b", source, re.I) or re.search(r"\b(keyboard|accessibility)\b", source, re.I))):
            raise ActionContractError("A keyboard action must be explicitly supported by the requirement; it cannot substitute for clicking a navigation control.")
        if key == "Escape" and control.get("in_popup"):
            if len(observation.get("interaction_context", {}).get("popups", [])) > 1:
                raise ActionContractError("More than one popup is visible; the dismissal target is ambiguous.")
            post = "popup_closed"
            expected = {"owner_expanded": control.get("expanded") == "true", "popup_count": len(observation.get("interaction_context", {}).get("popups", []))}
        elif key == "Tab":
            post = "focus_moved"
        elif step.expected_change in {"url", "content"}:
            post = "content_changed"
        elif any(check.requirement_id == requirement.get("id") and check.after_step == index for check in configuration.objective_checks):
            post = "configured_check"
        else:
            raise ActionContractError("The keyboard step has no defined observable result to verify.")
    elif purpose == "activate":
        if role not in {"button", "link", "tab", "menuitem", "option", "checkbox", "radio", "switch"}:
            raise ActionContractError("Activation requires an observed interactive control; text fields must use text entry.")
        if step.purpose != "auto" and step.expected_change in {"none", "auto"} and not any(
            check.requirement_id == requirement.get("id") and check.after_step == index for check in configuration.objective_checks
        ):
            raise ActionContractError("The activation step needs an expected change or a configured result check.")
        updates = {"action": "click"}
        post = "content_changed" if step.expected_change in {"url", "content"} else "action_completed"
    elif purpose == "inspect":
        if action not in {"read", "observe", "screenshot", "wait"}:
            raise ActionContractError("Inspection cannot perform a mutating browser action.")
    else:
        raise ActionContractError("The action purpose is unsupported.")
    updates["purpose"] = purpose
    compiled = step.model_copy(update=updates)
    return BoundAction(
        compiled, purpose, post, expected, identity, action,
        compiled.action != action or compiled.value != step.value,
        observation.get("observation_id"), step.value if action == "press" else None,
    )


async def verify_action(bound, session, page, after, before):
    if bound.purpose == "navigate" and not (
        after.get("accessible_ui", "").strip() or after.get("controls")
        or any(image.get("loaded") and image.get("width", 0) > 0 for image in after.get("content_images", []))
    ):
        raise ActionContractError("The destination URL was reached, but the page stayed blank; navigation could not be verified.")
    if after.get("evidence_integrity") != "verified":
        raise ActionContractError("The action result has no verified page checkpoint.")
    post = bound.postcondition
    if post == "destination_url":
        if not destination_matches(bound.expected, page.url):
            raise ActionContractError("The action did not reach its observed destination URL.")
    elif post in {"destination_observed", "content_changed"}:
        if after.get("url") == before.get("url") and after.get("visual_content_hash") and after.get("visual_content_hash") == before.get("visual_content_hash"):
            raise ActionContractError("The action did not produce its required observable page change.")
    elif post in {"value_matches", "option_matches", "checked_matches"}:
        matches = [item for item in after["controls"] if control_identity(item) == bound.identity]
        if len(matches) != 1:
            raise ActionContractError("The result control is missing or ambiguous after the action.")
        target = await session.resolve_element(matches[0]["element_id"])
        if post == "value_matches":
            valid = await target.read_value() == bound.expected
        elif post == "option_matches":
            valid = await target.read_options() == [bound.expected]
        else:
            valid = checked(matches[0].get("checked")) == bound.expected
        if not valid:
            raise ActionContractError("The control did not reach the planned value or state.")
    elif post == "control_focused":
        matches = [item for item in after["controls"] if control_identity(item) == bound.identity]
        if len(matches) != 1 or not matches[0].get("focused"):
            raise ActionContractError("The intended control did not receive focus.")
    elif post == "popup_closed":
        popups = after.get("interaction_context", {}).get("popups", [])
        if popups:
            raise ActionContractError("The popup did not close after the dismissal action.")
        if bound.expected.get("owner_expanded"):
            matches = [item for item in after["controls"] if control_identity(item) == bound.identity]
            if len(matches) != 1 or matches[0].get("expanded") != "false":
                raise ActionContractError("The expanded control did not collapse after dismissal.")
    elif post == "focus_moved":
        focused = [item for item in after["controls"] if item.get("focused")]
        if len(focused) != 1 or control_identity(focused[0]) == bound.identity:
            raise ActionContractError("Keyboard focus did not move to another observed control.")
    if not await session.capture_is_consistent(page, after):
        raise ActionContractError("The page changed while the action result was being verified.")
    return {
        "status": "verified" if post not in {"action_completed", "configured_check"} else "completed",
        "observation_id": after["observation_id"], "criterion_assessment": "separate",
    }
