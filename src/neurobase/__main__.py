"""Allow ``python -m neurobase`` to run the CLI."""

from __future__ import annotations

from neurobase.cli import app

if __name__ == "__main__":
    app()
