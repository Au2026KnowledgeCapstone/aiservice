"""OpenAI-compatible structured-output transport used by the MVP engine."""
import base64
import json
import re
from urllib.parse import urlsplit

import httpx

from app.provider import ProviderResponse


class OpenAITransport:
    """One request per structured call; never retries or logs prompts/secrets."""

    name = "openai"

    def __init__(self, config, transport=None):
        parsed = urlsplit(config.ai_api_url)
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
        ):
            raise ValueError("AI API must use an HTTPS base URL without embedded credentials.")
        if not config.ai_api_key.strip() or not config.ai_model.strip():
            raise ValueError("AI API key and model must be configured together.")
        self.base_url = config.ai_api_url.rstrip("/")
        self.api_key = config.ai_api_key
        self.model = config.ai_model
        self.timeout = config.ai_timeout_seconds
        self.transport = transport

    @staticmethod
    def _content(system, user, images):
        if not images:
            return user
        content = [{"type": "text", "text": user}]
        for image in images:
            if not isinstance(image, bytes):
                raise ValueError("Provider image must be bytes.")
            content.append({
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode("ascii")},
            })
        return content

    @staticmethod
    def _strict_schema(schema):
        """OpenAI strict mode requires every object property to be required."""
        normalized = json.loads(json.dumps(schema))

        def visit(node):
            if isinstance(node, dict):
                node.pop("default", None)
                properties = node.get("properties")
                if isinstance(properties, dict):
                    node["required"] = list(properties)
                    node["additionalProperties"] = False
                    for child in properties.values():
                        visit(child)
                for key, child in node.items():
                    if key != "properties":
                        visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)

        visit(normalized)
        return normalized

    async def request(self, *, operation, system, user, schema, images=()):
        schema_name = re.sub(r"[^a-zA-Z0-9_-]", "_", operation)[:64] or "structured_output"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": self._content(system, user, images)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": self._strict_schema(schema)},
            },
            "store": False,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport,
                follow_redirects=False, trust_env=False,
            ) as client:
                response = await client.post(
                    self.base_url + "/chat/completions",
                    headers={"Authorization": "Bearer " + self.api_key}, json=body,
                )
            if response.status_code != 200:
                raise RuntimeError("AI provider rejected or failed the request.")
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
            if choice.get("finish_reason") != "stop" or message.get("refusal"):
                raise RuntimeError("AI provider returned a refused or incomplete response.")
            result = json.loads(message["content"])
            if not isinstance(result, dict):
                raise ValueError("Structured response must be an object.")
            usage = payload.get("usage") or {}
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            if type(input_tokens) is not int or input_tokens < 0:
                input_tokens = None
            if type(output_tokens) is not int or output_tokens < 0:
                output_tokens = None
            return ProviderResponse(result, input_tokens, output_tokens)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise RuntimeError("AI provider failed or returned an invalid structured response.") from None
