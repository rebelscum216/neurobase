---
slug: phase-0-bootstrap
status: awaiting-review
author: claude
reviewer: codex
branch: phase-0-bootstrap
diff: git diff main...phase-0-bootstrap
created: 2026-07-07
---

# Review: Phase 0 repo bootstrap — installable package, CLI, tests, CI

## Brief  _(Author — Claude)_

**Intent.** Stand up the repo skeleton: an installable `neurobase-cli` package
with a live Typer CLI (`version` works; the rest of the planned command surface
is honest, phase-labeled stubs), a Phase 0 smoke-test suite, and a 3-OS CI
matrix. This is the *bootstrap sub-deliverable* of Phase 0 — see build-plan §6
Phase 0 — not the full phase (spikes S1/S2/S5/S6 are tracked separately below).

**Scope.** Branch `phase-0-bootstrap` (commit `bb2ea9f`), `git diff
main...phase-0-bootstrap`. Key files:
- `pyproject.toml` — package `neurobase-cli`, command `neurobase`, hatchling
  src-layout build, dev dependency group.
- `src/neurobase/cli/__init__.py` — Typer app; `version` live, rest stubbed.
- `src/neurobase/{core,brain,curator,adapters,recommender,mcp}/` — docstring-only
  stubs naming their spec section + owning phase.
- `tests/test_cli.py` — 5 smoke tests.
- `.github/workflows/ci.yml` — 3-OS × 2-Python matrix (ruff, format, mypy, pytest).
- `AGENTS.md` — retired `[not yet]` markers for what now exists; recorded
  bootstrap-done / spikes-pending state.

**Focus areas.** Whether the package is genuinely installable end-to-end
(`uv tool install .` → `neurobase --help`); whether the stub modules honestly
represent "not implemented yet" rather than silently faking behavior; whether
CI actually gates on all four checks.

**Known risks / tradeoffs.** Phase 0 per build-plan §6 also requires spikes
S1/S2/S5/S6 executed and written up as ADRs before the phase is "done." Those
spikes need the `claude` and `codex` CLIs installed and logged in; neither was
on `PATH` on this dev machine when this branch was built (only a
plugin-bundled Codex under `~/.codex/plugins`). S1 and S5 are partially
narrowed already (see build-plan §5 spike table) but not written up as ADRs;
S2 and S6 are untouched. This branch deliberately does not claim to close
Phase 0 as a whole.

**How to verify.** `uv sync && uv run pytest && uv run ruff check . && uv run
mypy src tests`; then `uv build && uv tool install .` and confirm `neurobase
--help` / `neurobase version` work from the installed shim.

**Out of scope.** Spike execution/ADR write-up (blocked on CLI availability,
tracked in build-plan §5); any Phase 1+ functionality (core store, curator,
brain, etc. — all still stubs by design).

---

## Reviewer findings  _(Reviewer — Codex)_

### F1 — Phase 0 spike/ADR gate not satisfied
- **severity:** major
- **location:** `docs/neurobase-build-plan.md:130`, `docs/adr/README.md:33`
- **issue:** Build-plan §6 Phase 0 requires spikes S1, S2, S5, S6 to be
  executed and written up as ADRs before Phase 0 is done, but `docs/adr/`
  contains only the template/index — no spike ADRs exist.
- **suggested direction:** Either add the spike ADRs, or explicitly scope this
  branch/review as "Phase 0 bootstrap only" and leave Phase 0 open.
- **resolution:** wontfix (as a blocker for *this* branch) — rescoped, not
  fixed by code. Taking the second option the reviewer offered: this branch
  was always the bootstrap sub-deliverable, not full Phase 0 closure —
  `AGENTS.md`'s "Current state" section already says so explicitly
  ("Remaining in Phase 0: spikes S1/S2/S5/S6 ... Each spike's outcome becomes
  an ADR"), and the spike table in build-plan §5 tracks per-spike status
  (S1/S5 narrowed, S2/S6 open, S4 closed) independently of this branch. Phase
  0 as a whole stays open — no ADRs will be written until `claude`/`codex`
  CLIs are available on a dev machine to actually re-run S1/S2/S5/S6. This is
  a real, tracked gap, not an oversight; merging this branch does not claim to
  close Phase 0.

### F2 — README stale: says "Not yet installable"
- **severity:** minor
- **location:** `README.md:16`, `README.md:48`
- **issue:** This branch adds a working package and `uv tool install .`
  succeeds, but the README still says "Not yet installable."
- **suggested direction:** Update the status/install section to distinguish
  "locally installable from source" from "not published yet."
- **resolution:** resolved — `README.md` status line now reads "Installable
  from source today; not yet published to PyPI," and the Install section
  leads with `uv tool install .` from a local checkout, with the PyPI-based
  command kept as the "once published" path.

**Verdict:** _not provided by reviewer in this pass (findings only)._
