"""Describe recorded visible UI without inferring actions, hidden data or outcomes.

Ported from BlindSpot/backend/app/services/browser/page_summary.py. Only
`describe_page`/`is_snapshot` are carried over — `readable_point` exists there
to upgrade legacy persisted display records for BE's UI, which has no
equivalent here (aiservice does not store requirements or verdicts).
"""
import json
import re

ROLE = re.compile(r'^\s*-\s*\'?(heading|button|textbox|searchbox|combobox|link|paragraph|text|alert|status)\b(?:\s*:?\s+("(?:\\.|[^"\\])*"|[^\n]*))?')


def is_snapshot(value):
    return any(re.match(r'^\s*-\s+(?:\'?[a-z]+\b|".+")', line) for line in value.splitlines())


def describe_page(value, expected="", url=""):
    if not value:
        return "No readable page state was captured."
    if not is_snapshot(value):
        return value[:500]
    if "\n" not in value:
        value = re.sub(r"\s+-\s+(?=\'?(?:img|heading|button|textbox|text|paragraph|link|status|alert)\b)", "\n- ", value)
    headings = []
    controls = []
    messages = []
    buttons = 0

    def append(items, label, limit):
        label = re.sub(r"^[+→←\s]+|[→←\s]+$", "", " ".join(label.split()).strip(" :"))
        if label and label not in items and len(items) < limit:
            items.append(label[:100])

    for line in value.splitlines():
        match = ROLE.match(line)
        if not match:
            continue
        role, raw = match.groups()
        raw = (raw or "").strip().strip(":").strip()
        if raw.startswith('"'):
            try:
                label = json.loads(raw)
            except ValueError:
                continue
        else:
            label = raw
        if role == "heading" and not re.match(r"^(good morning|good afternoon|good evening|hello)\b", label, re.I):
            append(headings, label, 5)
        elif role in {"textbox", "searchbox", "combobox"}:
            append(controls, label if label else "unnamed input", 5)
        elif role in {"button", "link"}:
            buttons += role == "button"
            if re.search(r"\b(add|view|open|search|save|submit|cancel|logout|log out|sign out|continue|next|back|delete|edit|load|refresh|send|message)\b", label, re.I):
                append(controls, label, 5)
        elif role in {"paragraph", "text", "alert", "status"} and label:
            if role in {"paragraph", "alert", "status"} or re.search(r"\b(coming soon|loading|no .* (available|found)|error|unable|failed)\b", label, re.I):
                append(messages, label, 2)

    def quoted(items):
        values = [f"\u201c{item}\u201d" for item in items]
        return " and ".join(values) if len(values) < 3 else ", ".join(values[:-1]) + " and " + values[-1]

    wanted = set(re.findall(r"\w+", expected.casefold()))
    headings.sort(key=lambda label: -len(set(re.findall(r"\w+", label.casefold())) & wanted))
    # Describe the relevant visible state in one sentence, rather than reciting the inventory.
    subject = "The page"
    if url:
        from urllib.parse import urlsplit
        path = urlsplit(url).path
        if path:
            subject += " at " + path[:100]
    if headings:
        sentence = subject + " showed " + quoted(headings[:2])
        if controls:
            sentence += ", with " + quoted(controls[:2]) + " controls visible"
        elif messages:
            sentence += ", with the message " + quoted(messages[:1])
    elif messages:
        sentence = subject + " showed " + quoted(messages[:1])
    elif controls:
        sentence = subject + " displayed " + quoted(controls[:2]) + " controls"
    elif buttons:
        sentence = subject + f" showed {buttons} " + ("button" if buttons == 1 else "buttons")
    else:
        sentence = "The page was captured, but its visible state could not be described from the recorded evidence"
    return sentence.rstrip(". ") + "."
