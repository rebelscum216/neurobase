"""Starlette app factory + server entry point for the local Suggestions
review web UI (Web UI Phase 1 plan, "Architecture").

This is a *local write surface* — see ``webui/security.py``. The bind is
hard-pinned to ``127.0.0.1``; there is no ``--host`` flag in this phase.
"""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.templating import Jinja2Templates

from neurobase.webui.routes import suggestions_routes
from neurobase.webui.security import CSRF_FORM_FIELD, CSRFMiddleware, new_csrf_token

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _store_root_label(root: Path) -> str:
    """The store path as shown in the rail brand badge: ``~``-collapsed
    when under the user's home directory (matches how every other Neurobase
    surface — the CLI, the vault docs — refers to a store), the resolved
    absolute path otherwise."""
    resolved = root.resolve()
    try:
        return f"~/{resolved.relative_to(Path.home())}"
    except ValueError:
        return str(resolved)


def build_app(root: Path) -> Starlette:
    """Build the Suggestions-review Starlette app rooted at a Neurobase
    store (``root``). Mounts Jinja2 templates from ``webui/templates/``,
    wires the same-origin/CSRF middleware, and the Suggestions route table
    (``routes.py``).
    """
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    # The hidden form field name every mutating template must use — a Jinja
    # global rather than a value every route handler has to thread through
    # its own context, so it can never drift out of sync with
    # ``security.CSRFMiddleware``'s expectation.
    templates.env.globals["CSRF_FORM_FIELD"] = CSRF_FORM_FIELD
    # The rail brand badge shows the real store path (base.html) — a Jinja
    # global for the same reason as CSRF_FORM_FIELD above.
    templates.env.globals["store_root_label"] = _store_root_label(root)

    app = Starlette(routes=suggestions_routes())
    app.state.root = root
    app.state.templates = templates
    app.state.csrf_token = new_csrf_token()
    app.add_middleware(CSRFMiddleware)
    return app


def serve(root: Path, *, port: int = 8765) -> None:
    """Run the web UI. Bind is always ``127.0.0.1`` — never ``0.0.0.0`` or
    left unspecified — this is a hard security requirement (Phase 1 plan,
    "Security": "Bind 127.0.0.1 only — never 0.0.0.0. No --host flag in
    phase 1.").
    """
    import uvicorn

    uvicorn.run(build_app(root), host="127.0.0.1", port=port)
