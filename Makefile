# Local dev shortcuts. The CI gate itself lives in scripts/ci.py so local dev
# and GitHub Actions can't drift — `make ci` just calls it. `make` isn't
# reliably present on the Windows CI runner, which is exactly why the gate is a
# plain Python script the workflow invokes directly on every OS in the matrix
# (see .github/workflows/ci.yml — ubuntu + macos; Windows parked 2026-07-20, and
# the gate stays make-free so re-adding it costs nothing). Use `make ci` locally;
# CI runs the same file.
.PHONY: ci sync fmt

ci:  ## Run the full CI gate (ruff + format + mypy + store-chokepoint + pytest w/ coverage floor + renderer), as CI does — needs uv and Node 22+
	uv run python scripts/ci.py

sync:  ## Install/refresh the managed dev environment
	uv sync

fmt:  ## Auto-fix lint and format in place (then re-run `make ci`)
	uv run ruff check --fix .
	uv run ruff format .
