"""Tests for the anthropic-api backend, using a fake injected client."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import anthropic
import httpx
import pytest

from neurobase.brain import anthropic_api
from neurobase.brain.anthropic_api import AnthropicAPIBrain, resolve_api_key
from neurobase.brain.base import BrainError


def _fake_keyring(value: str | None = None, *, raises: bool = False) -> types.ModuleType:
    module = types.ModuleType("keyring")

    def get_password(service: str, username: str) -> str | None:
        if raises:
            raise RuntimeError("no keyring backend")
        return value

    module.get_password = get_password  # type: ignore[attr-defined]
    return module


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _Response:
    content: list


class _FakeMessages:
    def __init__(self, behavior) -> None:
        self._behavior = behavior
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._behavior(kwargs, len(self.calls))


class _FakeClient:
    def __init__(self, behavior) -> None:
        self.messages = _FakeMessages(behavior)


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def test_text_joins_text_blocks() -> None:
    client = _FakeClient(lambda kwargs, n: _Response([_TextBlock("hello "), _TextBlock("world")]))
    brain = AnthropicAPIBrain(client=client)
    assert brain.text("sys", "user") == "hello world"


def test_text_skips_non_text_blocks() -> None:
    @dataclass
    class _ThinkingBlock:
        type: str = "thinking"
        thinking: str = "hmm"

    client = _FakeClient(lambda kwargs, n: _Response([_ThinkingBlock(), _TextBlock("answer")]))
    assert AnthropicAPIBrain(client=client).text("sys", "user") == "answer"


def test_plan_json_parses() -> None:
    client = _FakeClient(
        lambda kwargs, n: _Response([_TextBlock('{"upserts": [], "tombstones": []}')])
    )
    assert AnthropicAPIBrain(client=client).plan_json("sys", "user") == {
        "upserts": [],
        "tombstones": [],
    }


def test_passes_model_system_and_user() -> None:
    client = _FakeClient(lambda kwargs, n: _Response([_TextBlock("ok")]))
    AnthropicAPIBrain(model="claude-sonnet-5", client=client).text("SYS", "USER")
    call = client.messages.calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert call["system"] == "SYS"
    assert call["messages"] == [{"role": "user", "content": "USER"}]


def test_timeout_retries_then_gives_up() -> None:
    def behavior(kwargs, n):
        raise anthropic.APITimeoutError(request=_req())

    client = _FakeClient(behavior)
    with pytest.raises(BrainError):
        AnthropicAPIBrain(client=client).text("sys", "user")
    assert len(client.messages.calls) == 2


def test_5xx_retries() -> None:
    def behavior(kwargs, n):
        raise anthropic.APIStatusError(
            "server error", response=httpx.Response(503, request=_req()), body=None
        )

    client = _FakeClient(behavior)
    with pytest.raises(BrainError):
        AnthropicAPIBrain(client=client).text("sys", "user")
    assert len(client.messages.calls) == 2


def test_4xx_does_not_retry() -> None:
    def behavior(kwargs, n):
        raise anthropic.APIStatusError(
            "bad request", response=httpx.Response(400, request=_req()), body=None
        )

    client = _FakeClient(behavior)
    with pytest.raises(BrainError):
        AnthropicAPIBrain(client=client).text("sys", "user")
    assert len(client.messages.calls) == 1  # non-retryable


def test_5xx_then_success_recovers() -> None:
    def behavior(kwargs, n):
        if n == 1:
            raise anthropic.APIStatusError(
                "overloaded", response=httpx.Response(529, request=_req()), body=None
            )
        return _Response([_TextBlock("recovered")])

    client = _FakeClient(behavior)
    assert AnthropicAPIBrain(client=client).text("sys", "user") == "recovered"
    assert len(client.messages.calls) == 2


def test_empty_text_retries() -> None:
    client = _FakeClient(lambda kwargs, n: _Response([]))
    with pytest.raises(BrainError):
        AnthropicAPIBrain(client=client).text("sys", "user")
    assert len(client.messages.calls) == 2


def test_resolve_api_key_env_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEUROBASE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Neutralize the keychain so this test only exercises env precedence.
    monkeypatch.setattr(anthropic_api, "_keychain_api_key", lambda: None)
    assert resolve_api_key() is None

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    assert resolve_api_key() == "sk-anthropic"

    monkeypatch.setenv("NEUROBASE_API_KEY", "sk-neurobase")
    assert resolve_api_key() == "sk-neurobase"  # NEUROBASE_API_KEY wins


def test_resolve_api_key_falls_back_to_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    """spec §10: env vars absent ⇒ consult the OS keychain."""
    monkeypatch.delenv("NEUROBASE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring("sk-from-keychain"))
    assert resolve_api_key() == "sk-from-keychain"


def test_env_var_wins_over_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring("sk-from-keychain"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    monkeypatch.delenv("NEUROBASE_API_KEY", raising=False)
    assert resolve_api_key() == "sk-from-env"


def test_keychain_error_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEUROBASE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(raises=True))
    assert resolve_api_key() is None


def test_keychain_missing_module_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEUROBASE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "keyring", None)  # ImportError on `import keyring`
    assert resolve_api_key() is None
