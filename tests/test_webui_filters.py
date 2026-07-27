"""Tests for the webui Jinja display filters (webui/filters.py)."""

from __future__ import annotations

import pytest

from neurobase.webui.filters import human_datetime


@pytest.mark.parametrize(
    ("iso", "expected"),
    [
        ("2026-07-15T13:20:33Z", "July 15, 2026<br>1:20pm"),  # date over time
        ("2026-07-15T12:07:00Z", "July 15, 2026<br>12:07pm"),  # noon is 12pm
        ("2026-07-15T00:07:00Z", "July 15, 2026<br>12:07am"),  # midnight is 12am
        ("2026-07-01T09:05:00Z", "July 1, 2026<br>9:05am"),  # no leading zero on day/hour
        ("2026-12-31T23:59:00+00:00", "December 31, 2026<br>11:59pm"),  # offset form parses too
    ],
)
def test_human_datetime_formats(iso: str, expected: str) -> None:
    assert human_datetime(iso) == expected


def test_human_datetime_empty_is_dash() -> None:
    assert human_datetime("") == "—"
    assert human_datetime(None) == "—"


def test_human_datetime_unparseable_shows_verbatim() -> None:
    # a malformed timestamp is shown, not hidden — so bad data is visible.
    assert human_datetime("not-a-date") == "not-a-date"
