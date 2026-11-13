"""Distinguish non-submit creation-flow entry controls from form submission.

Ported from BlindSpot/backend/app/services/browser/form_entries.py, unchanged
except for the `control_identity` import.
"""
import re

from engines.mvp.repeatability import control_identity

ENTRY = re.compile(r"^\W*(?:add|new|create)\s+[\w][\w\s-]{0,65}$", re.I)
DANGEROUS = re.compile(r"\b(save|submit|send|delete|remove|pay|purchase|approve|reject|upload|download)\b", re.I)


def is_form_entry(control):
    # A button inside a form is never probed as a creation-flow entry.
    return bool(
        control
        and control.get("role") in {"button", "link"}
        and not control.get("has_form")
        and ENTRY.fullmatch(control.get("name", ""))
        and not DANGEROUS.search(control.get("name", ""))
    )


def entry_contracts(discovery):
    states = {s["observation_id"]: s for s in discovery.get("states", []) if s.get("evidence_integrity") == "verified"}
    result = []
    for edge in discovery.get("transitions", []):
        before = states.get(edge.get("from"))
        after = states.get(edge.get("to"))
        if not before or not after or edge.get("status") != "observed" or not edge.get("form_entry_probe"):
            continue
        result.append({
            "source_url": before["url"],
            "source_hash": before["visual_content_hash"],
            "control": edge["control"],
            "destination_url": after["url"],
            "effect": edge["effect"],
        })
    return result


def matched_entry(control, observation):
    if not is_form_entry(control):
        return None
    matches = [
        entry
        for entry in observation.get("form_entry_contracts", [])
        if entry["source_url"] == observation["url"]
        and entry["source_hash"] == observation["visual_content_hash"]
        and entry["control"] == control_identity(control)
    ]
    return matches[0] if len(matches) == 1 else None
