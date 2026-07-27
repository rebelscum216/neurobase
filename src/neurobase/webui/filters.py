"""Jinja template filters shared across the webui, registered on the environment
in ``app.py`` (like the ``store_root_label`` / ``CSRF_FORM_FIELD`` globals) so no
route handler formats display values by hand."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from markupsafe import Markup, escape


def human_datetime(value: Any) -> str:
    """Render an ISO-8601 timestamp on two lines — the date (``July 15, 2026``)
    over the time (``1:20pm``) — full month name, no leading zero on the day or
    hour, 12-hour clock, lowercase am/pm.

    Deliberately avoids ``strftime('%-d')``/``'%-I'`` (the no-pad directives are
    POSIX-only and raise on Windows, where CI also runs) — the day, hour, and
    meridiem are composed by hand so the output is identical on every OS. Returns
    ``Markup`` (the ``<br>`` is intentional markup; the date/time are computed
    from a parsed datetime, never raw input, and escaped for good measure). An
    empty value renders as ``—``; an unparseable one is shown verbatim (escaped
    by Jinja) rather than hidden, so a malformed timestamp stays visible."""
    if not isinstance(value, str) or not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    hour = dt.hour % 12 or 12
    meridiem = "am" if dt.hour < 12 else "pm"
    date = f"{dt.strftime('%B')} {dt.day}, {dt.year}"
    time = f"{hour}:{dt.minute:02d}{meridiem}"
    # Two blocks (date over time), each nowrap so a narrow column can't wrap the
    # year onto its own line — see the .hd-date/.hd-time rules in base.html.
    return Markup('<span class="hd-date">{}</span><span class="hd-time">{}</span>').format(
        escape(date), escape(time)
    )
