import pytest

from memocore.adapters.llm.base import (
    ChatMessage,
    ChatRequest,
    ExtractionError,
    StructuredOutputMode,
)
from memocore.adapters.llm.ollama_provider import OllamaProvider


class MockResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class MockClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json):
        return self.response


async def test_ollama_provider_valid_json(monkeypatch):
    response = MockResponse({"message": {"content": '{"summary":"Saved","tasks":[]}'}})
    monkeypatch.setattr(
        "httpx.AsyncClient", lambda timeout: MockClient(response)
    )

    provider = OllamaProvider("http://ollama", "model")
    result = await provider.chat(
        ChatRequest(
            messages=[ChatMessage(role="user", content="hello")],
            response_format=StructuredOutputMode.JSON_MODE,
        )
    )

    assert result.content == '{"summary":"Saved","tasks":[]}'


async def test_ollama_provider_missing_content(monkeypatch):
    response = MockResponse({"message": {}})
    monkeypatch.setattr(
        "httpx.AsyncClient", lambda timeout: MockClient(response)
    )

    provider = OllamaProvider("http://ollama", "model")

    with pytest.raises(ExtractionError):
        await provider.chat(ChatRequest(messages=[ChatMessage(role="user", content="hello")]))
