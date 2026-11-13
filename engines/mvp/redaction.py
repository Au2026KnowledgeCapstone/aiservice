"""Ported from BlindSpot/backend/app/services/browser/redaction.py, unchanged."""
import re


class Redactor:
    def __init__(self, credentials=None):
        self.secrets = sorted([value for value in (credentials or {}).values() if value], key=len, reverse=True)

    def text(self, value, limit=20000):
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, "[redacted]")
        text = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[email]", text)
        text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[token]", text)
        return text[:limit]
