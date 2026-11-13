import base64
import json

import httpx
import pytest

from app.ai_transport import OpenAITransport
from app.core.config import Settings


def config(**values):
    return Settings(database_url="sqlite://", service_token="service", ai_api_key="api-secret", **values)


async def test_structured_request_includes_schema_images_and_usage():
    captured = {}

    def respond(request):
        captured["request"] = request
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            "usage": {"prompt_tokens": 18, "completion_tokens": 5},
        })

    client = OpenAITransport(config(), transport=httpx.MockTransport(respond))
    result = await client.request(
        operation="GoalAssessment", system="Rules", user="Inspect this", schema={"type": "object"},
        images=(b"png-data",),
    )
    request = captured["request"]
    body = json.loads(request.content)
    content = body["messages"][1]["content"]
    assert request.url == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer api-secret"
    assert body["response_format"]["json_schema"] == {
        "name": "GoalAssessment", "strict": True, "schema": {"type": "object"},
    }
    assert content[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(b"png-data").decode()
    assert result.result == {"ok": True}
    assert (result.input_tokens, result.output_tokens, result.amount) == (18, 5, None)


@pytest.mark.parametrize("payload", [
    {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
    {"choices": [{"finish_reason": "stop", "message": {"refusal": "no", "content": "{}"}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]},
])
async def test_refused_incomplete_or_non_object_responses_fail_safely(payload):
    client = OpenAITransport(config(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    with pytest.raises(RuntimeError, match="AI provider") as error:
        await client.request(operation="x", system="private", user="private", schema={})
    assert "private" not in str(error.value)


def test_transport_requires_key_and_secure_base_url():
    with pytest.raises(ValueError, match="key and model"):
        OpenAITransport(Settings(database_url="sqlite://"))
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAITransport(config(ai_api_url="http://localhost:8080/v1"))
