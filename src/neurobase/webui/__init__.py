"""Local, loopback-only web UI for reviewing recommender proposals (Web UI
Phase 1 plan). A peer of ``cli/`` — both sit on top of ``core``/``brain``/the
mid tier; neither imports the other (layer contract's "no lateral import
between edges" rule).

This package is lazily imported by the CLI's ``ui`` command so starlette,
jinja2, and uvicorn never load on the hook fast path or any other command's
cold start.
"""

from __future__ import annotations

from neurobase.webui.app import build_app, serve

__all__ = ["build_app", "serve"]
