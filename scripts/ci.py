#!/usr/bin/env python3
"""The one CI gate for Neurobase — the single source of truth for "green".

Runs the exact checks CI enforces, in order, each via ``uv run`` so the command
is identical whether you invoke it locally or on a GitHub Actions runner:

    ruff check .            # lint
    ruff format --check .   # formatting
    mypy src tests          # types
    pytest --cov …          # tests + coverage (fails under the pyproject floor)
    node --test tests/js/   # the shipped renderer's behaviour (see below)

Both local dev (``make ci`` / this script) and every matrix job in
``.github/workflows/ci.yml`` call this file, so the two can never drift: add or
change a check *here* and every runner on every OS picks it up. This is the
guardrail against pushing after running only part of the gate locally.

Usage:
    uv run python scripts/ci.py         # run the full gate
    make ci                             # same thing (local dev convenience)

Every check runs even if an earlier one fails, so one pass surfaces *all* the
problems. Exits 0 only when every check passes; non-zero otherwise, after
printing a summary.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# THE gate. This list is the single source of truth shared by local dev and CI.
# Keep it in lockstep with the "Dev workflow" section of AGENTS.md.
# Each entry is (human label, argv). `uv run` makes each command match CI byte
# for byte and work even when the venv isn't already active.
# ---------------------------------------------------------------------------
CHECKS: list[tuple[str, list[str]]] = [
    ("ruff check", ["uv", "run", "ruff", "check", "."]),
    ("ruff format --check", ["uv", "run", "ruff", "format", "--check", "."]),
    ("mypy src tests", ["uv", "run", "mypy", "src", "tests"]),
    # ADR-0015 step 5: forbid raw-root store/registry access in production (src/)
    # outside the three exempt core modules — closes G1's accessor class (the
    # lifecycle commands are command-guarded; see known-gaps / spec §10).
    ("store-chokepoint", ["uv", "run", "python", "scripts/check_store_chokepoint.py"]),
    # Coverage rides along with the test run rather than being a separate check:
    # a second pytest invocation would double the slowest step for no new signal.
    # The floor itself (`fail_under`) lives in pyproject.toml's [tool.coverage.report]
    # alongside the note on *which* metric it gates — coverage.py enforces the
    # combined `Cover` figure, not branch or statement coverage. Falling below it
    # exits non-zero, so this entry fails the gate exactly like a failing test.
    (
        "pytest --cov",
        [
            "uv",
            "run",
            "pytest",
            "--cov=src/neurobase",
            "--cov-branch",
            "--cov-report=term-missing",
        ],
    ),
    # `templates/graph.html` ships ~700 lines of client logic that Python cannot
    # execute, and round 2 of the Phase G review proved source assertions about it
    # are worthless: a regex test stayed green while the O(n) layout claim it
    # named was false at 66.7M scans / 4,000 nodes. So the renderer gets a real
    # behaviour gate — bounded layout work, park/wake scheduling, prototype-key
    # safety — driven against the shipped script.
    #
    # Node is a DECLARED dependency of the gate, not an optional extra: a test
    # that skips when a runtime is missing is vacuity with extra steps, which is
    # the exact failure this suite exists to close. `main()` fails the gate if
    # node is absent rather than passing quietly. Still zero-install — `node
    # --test` and two files, no npm, no package.json, no node_modules.
    ("node --test tests/js", ["node", "--test", "tests/js/*.test.mjs"]),
]

# Tools the gate cannot run without. `uv` has always been one; `node` became one
# when the renderer got a behaviour suite (above).
REQUIRED_TOOLS = {
    "uv": "https://docs.astral.sh/uv/",
    "node": "https://nodejs.org/ (v18+, for `node --test` — no npm install needed)",
}


def main() -> int:
    missing = [(tool, url) for tool, url in REQUIRED_TOOLS.items() if shutil.which(tool) is None]
    if missing:
        for tool, url in missing:
            print(
                f"error: `{tool}` is not on PATH. Install it — {url} — then re-run.",
                file=sys.stderr,
            )
        return 127

    results: list[tuple[str, bool, float]] = []
    for label, argv in CHECKS:
        print(f"\n==> {label}  ({' '.join(argv)})", flush=True)
        start = time.perf_counter()
        completed = subprocess.run(argv)  # noqa: S603 — fixed, trusted argv
        elapsed = time.perf_counter() - start
        results.append((label, completed.returncode == 0, elapsed))

    print("\n" + "=" * 60)
    print("CI gate summary")
    print("=" * 60)
    for label, ok, elapsed in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}  ({elapsed:.1f}s)")

    failed = [label for label, ok, _ in results if not ok]
    if failed:
        print(f"\n{len(failed)} check(s) failed: {', '.join(failed)}")
        print("Fix these before pushing — CI runs this exact gate on every OS.")
        return 1

    print("\nAll checks passed. Safe to push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
