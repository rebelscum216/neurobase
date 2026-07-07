"""Anthropic API backend.

Uses the official ``anthropic`` SDK's Messages API. Unlike the CLI backends
(which run the user's own logged-in CLI), this one authenticates with an API
key sourced per spec §10: ``NEUROBASE_API_KEY`` > ``ANTHROPIC_API_KEY``. The
API backend uses the configured model (spec §10 ``[brain].model``, default
``claude-sonnet-5``); CLI backends use the CLI's own.

To keep all three backends behaviorally uniform, this one also just prompts
for JSON and lenient-parses (no structured-output / thinking config) — the
curator's parse-failure safety net is the same everywhere.
"""

from __future__ import annotations

import os
from typing import Any

from neurobase.brain.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    BrainError,
    BrainUnavailableError,
    RetryableBrainError,
    call_with_retry,
    parse_plan_json,
)

DEFAULT_API_MODEL = "claude-sonnet-5"


def resolve_api_key() -> str | None:
    """API-key precedence for API backends (spec §10): ``NEUROBASE_API_KEY``
    then ``ANTHROPIC_API_KEY``. ``None`` ⇒ backend unavailable (auto-detection
    falls through)."""
    return os.environ.get("NEUROBASE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")


class AnthropicAPIBrain:
    """Anthropic Messages API backend. ``client`` is injectable so tests never
    touch the network or need a real key."""

    name = "anthropic-api"

    def __init__(
        self,
        *,
        model: str = DEFAULT_API_MODEL,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        api_key: str | None = None,
        client: Any = None,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._api_key = api_key
        self._client = client

    def _client_or_create(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - anthropic is a core dep
            raise BrainUnavailableError("anthropic SDK not installed") from exc
        key = self._api_key or resolve_api_key()
        if not key:
            raise BrainUnavailableError("no API key (set NEUROBASE_API_KEY or ANTHROPIC_API_KEY)")
        self._client = anthropic.Anthropic(api_key=key, timeout=self._timeout)
        return self._client

    def _once(self, system: str, user: str) -> str:
        import anthropic

        client = self._client_or_create()
        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise RetryableBrainError(f"anthropic API transport error: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise RetryableBrainError(f"anthropic API {exc.status_code}") from exc
            raise BrainError(f"anthropic API {exc.status_code}: {exc}") from exc
        parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
        answer = "".join(parts)
        if not answer:
            raise RetryableBrainError("anthropic API returned no text content")
        return answer

    def text(self, system: str, user: str) -> str:
        return call_with_retry(lambda: self._once(system, user))

    def plan_json(self, system: str, user: str) -> dict:
        return call_with_retry(lambda: parse_plan_json(self._once(system, user)))
