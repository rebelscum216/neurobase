"""Same-origin + per-process CSRF protection for the local web UI (Web UI
Phase 1 plan, "Security"). This is a local *write* surface — every mutating
request (POST) must prove it originated from the app's own page, not a
cross-site form or script.

No cookies, no sessions, no dependency beyond starlette itself. The token
lives only in server memory (``app.state.csrf_token``) for the process
lifetime — a restart invalidates every outstanding form, which is the correct
behavior for a single-user local tool.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

# The hidden form field every mutating (POST) form must carry.
CSRF_FORM_FIELD = "csrf_token"


def new_csrf_token() -> str:
    """A fresh, unguessable per-process CSRF token."""
    return secrets.token_urlsafe(32)


def _origin_netloc(request: Request) -> str | None:
    """The ``host[:port]`` the request claims to have come from: the
    ``Origin`` header, falling back to ``Referer`` when ``Origin`` is absent
    (some browsers omit ``Origin`` on same-origin form submissions)."""
    header = request.headers.get("origin") or request.headers.get("referer")
    if not header:
        return None
    return urlsplit(header).netloc or None


async def check_same_origin_csrf(request: Request, csrf_token: str) -> str | None:
    """Validate one mutating (POST) request against the same-origin + CSRF
    rules. Returns ``None`` when the request passes, or a short human-
    readable rejection reason when it should be answered with 403.

    Pure and side-effect-free beyond consuming (and caching) the request
    body — safe to call directly from a route handler or a test, not just
    from :class:`CSRFMiddleware`.

    Reads ``request.body()`` *before* ``request.form()``: Starlette's
    ``BaseHTTPMiddleware`` only replays a POST body to the downstream route
    handler when ``Request.body()`` has populated ``request._body`` — a
    bare ``await request.form()`` drains the ASGI receive stream directly
    without setting it, so a route handler's own later ``await
    request.form()`` would otherwise see an empty body and lose every field
    but the one this check already happened to read (e.g. a proposal
    accept's ``target`` or an edit's ``draft``). Priming the cache here
    keeps this middleware-level check side-effect-neutral for every
    downstream handler in ``webui/routes.py``.
    """
    origin_netloc = _origin_netloc(request)
    host = request.headers.get("host")
    if origin_netloc is None or host is None or origin_netloc != host:
        return "cross-origin request rejected"
    await request.body()
    form = await request.form()
    submitted = form.get(CSRF_FORM_FIELD)
    if not isinstance(submitted, str) or not secrets.compare_digest(submitted, csrf_token):
        return "missing or invalid csrf_token"
    return None


class CSRFMiddleware(BaseHTTPMiddleware):
    """Rejects every POST that fails :func:`check_same_origin_csrf` with 403,
    before it reaches any route handler. Reads the token from
    ``request.app.state.csrf_token`` at dispatch time (set once, at
    app-build time, by ``webui.app.build_app``) rather than capturing it at
    construction time, so it stays correct regardless of when Starlette
    happens to build its lazy middleware stack.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "POST":
            csrf_token = request.app.state.csrf_token
            rejection = await check_same_origin_csrf(request, csrf_token)
            if rejection is not None:
                return PlainTextResponse(rejection, status_code=403)
        return await call_next(request)
