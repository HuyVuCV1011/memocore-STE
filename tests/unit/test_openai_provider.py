import httpx
import pytest

from memocore.adapters.llm.base import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ExtractionError,
    StructuredOutputMode,
)
from memocore.adapters.llm.openai_provider import OpenAICompatibleProvider


class MockResponse:
    def __init__(self, body, *, status_code=200):
        self.body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=httpx.Request("POST", "http://test"), response=self,
            )

    def json(self):
        return self.body


class MockClient:
    def __init__(self, response):
        self.response = response
        self.last_payload = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, *, headers=None, json=None):
        self.last_payload = json
        return self.response


VALID_BODY = {
    "choices": [{"message": {"content": '{"summary":"test"}'}}],
    "model": "test-model",
    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
}


def _make_provider():
    return OpenAICompatibleProvider(
        provider_name="openai",
        base_url="http://test-api",
        model="test-model",
        api_key="sk-test",
    )


def _make_request(**overrides):
    defaults = dict(
        messages=[ChatMessage(role="user", content="hello")],
        response_format=StructuredOutputMode.JSON_MODE,
    )
    defaults.update(overrides)
    return ChatRequest(**defaults)


async def test_openai_provider_valid_json_response(monkeypatch):
    mock_client = MockClient(MockResponse(VALID_BODY))
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: mock_client)

    provider = _make_provider()
    result = await provider.chat(_make_request())

    assert isinstance(result, ChatResponse)
    assert result.content == '{"summary":"test"}'
    assert result.model == "test-model"
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 20}
    assert result.raw_response == VALID_BODY


async def test_openai_provider_json_schema_mode(monkeypatch):
    mock_client = MockClient(MockResponse(VALID_BODY))
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: mock_client)

    provider = _make_provider()
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
    request = _make_request(
        response_format=StructuredOutputMode.JSON_SCHEMA,
        json_schema=schema,
    )
    await provider.chat(request)

    payload = mock_client.last_payload
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["name"] == "note_extraction"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["response_format"]["json_schema"]["schema"] == schema


async def test_openai_provider_missing_choices(monkeypatch):
    body = {"model": "test-model", "usage": {}}
    mock_client = MockClient(MockResponse(body))
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: mock_client)

    provider = _make_provider()
    with pytest.raises(ExtractionError, match="did not include choices"):
        await provider.chat(_make_request())


async def test_openai_provider_http_error(monkeypatch):
    mock_client = MockClient(MockResponse({}, status_code=500))
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: mock_client)

    provider = _make_provider()
    with pytest.raises(ExtractionError, match="request failed"):
        await provider.chat(_make_request())
