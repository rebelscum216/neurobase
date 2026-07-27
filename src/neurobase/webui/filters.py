"""Jinja template filters shared across the webui, registered on the environment
in ``app.py`` (like the ``store_root_label`` / ``CSRF_FORM_FIELD`` globals) so no
route handler formats display values by hand."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def human_datetime(value: Any) -> str:
    """Render an ISO-8601 timestamp as ``July 15, 2026 | 1:20pm`` — full month
    name, no leading zero on the day or hour, 12-hour clock, lowercase am/pm.

    Deliberately avoids ``strftime('%-d')``/``'%-I'`` (the no-pad directives are
    POSIX-only and raise on Windows, where CI also runs) — the day, hour, and
    meridiem are composed by hand so the output is identical on every OS. An
    empty value renders as ``—``; an unparseable one is shown verbatim rather
    than hidden, so a malformed timestamp is visible rather than silently blank."""
    if not isinstance(value, str) or not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    hour = dt.hour % 12 or 12
    meridiem = "am" if dt.hour < 12 else "pm"
    return f"{dt.strftime('%B')} {dt.day}, {dt.year} | {hour}:{dt.minute:02d}{meridiem}"
