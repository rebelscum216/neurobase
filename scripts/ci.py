#!/usr/bin/env python3
"""The one CI gate for Neurobase — the single source of truth for "green".

Runs the exact checks CI enforces, in order, each via ``uv run`` so the command
is identical whether you invoke it locally or on a GitHub Actions runner:

    ruff check .            # lint
    ruff format --check .   # formatting
    mypy src tests          # types
    pytest --cov …          # tests + coverage (fails under the pyproject floor)

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
    # outside the three exempt core modules — closes G1's accessor class (two
    # lifecycle paths remain, tracked for step 4d; see known-gaps).
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
]


def main() -> int:
    if shutil.which("uv") is None:
        print(
            "error: `uv` is not on PATH. Install it — https://docs.astral.sh/uv/ — then re-run.",
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
