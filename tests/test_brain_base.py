"""Tests for the brain contract helpers (base.py)."""

from __future__ import annotations

import pytest

from neurobase.brain import base


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"upserts": [], "tombstones": []}', {"upserts": [], "tombstones": []}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('   {"a": 1}   ', {"a": 1}),
    ],
)
def test_parse_plan_json_lenient(raw: str, expected: dict) -> None:
    assert base.parse_plan_json(raw) == expected


@pytest.mark.parametrize("bad", ["not json at all", "[1, 2, 3]", '"a string"', "42", ""])
def test_parse_plan_json_rejects_non_object(bad: str) -> None:
    with pytest.raises(base.RetryableBrainError):
        base.parse_plan_json(bad)


def test_combine_prompt() -> None:
    assert base.combine_prompt("SYS", "USER") == "SYS\n\n---\n\nUSER"


def test_call_with_retry_succeeds_first_try() -> None:
    calls = []

    def attempt() -> str:
        calls.append(1)
        return "ok"

    assert base.call_with_retry(attempt) == "ok"
    assert len(calls) == 1


def test_call_with_retry_retries_once_then_succeeds() -> None:
    calls = []

    def attempt() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise base.RetryableBrainError("transient")
        return "ok"

    assert base.call_with_retry(attempt) == "ok"
    assert len(calls) == 2


def test_call_with_retry_exhausts_and_raises_brain_error() -> None:
    calls = []

    def attempt() -> str:
        calls.append(1)
        raise base.RetryableBrainError("always")

    with pytest.raises(base.BrainError):
        base.call_with_retry(attempt)
    assert len(calls) == 2  # 1 try + 1 retry


def test_call_with_retry_does_not_retry_non_retryable() -> None:
    calls = []

    def attempt() -> str:
        calls.append(1)
        raise base.BrainError("fatal")

    with pytest.raises(base.BrainError):
        base.call_with_retry(attempt)
    assert len(calls) == 1
