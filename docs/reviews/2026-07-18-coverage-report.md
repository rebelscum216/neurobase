---
slug: coverage-report
status: approved
author: claude
reviewer: codex
branch: fix-curator-runaway-guard-lock
diff: git diff main...HEAD
created: 2026-07-18
---

# Review: full-repo coverage report (2026-07-18)

## Brief  _(Author — Claude)_

**Intent.** A single committed doc — `docs/notes/2026-07-18-coverage-report.md` —
capturing an out-of-band, repo-wide coverage crawl and a prioritized gap list.
This is a **factual-claims review**: the value of the doc is only as good as the
numbers and line references in it, so the job is to verify them, not to judge
prose.

**Scope.** Branch `fix-curator-runaway-guard-lock`, `git diff main...HEAD`. Key
files:
- `docs/notes/2026-07-18-coverage-report.md` — the only committed change; the
  coverage report under review.

**Focus areas.** Where I most want your eyes:
1. **Reproduce the headline numbers.** Run:
   `uv run --with coverage --with pytest-timeout coverage run --branch -m pytest -q`
   then `uv run --with coverage coverage report --include='src/*'`.
   Confirm ~91% line / ~89% branch and the 551 test count. Note: run it against
   this branch's **working tree** (which carries the uncommitted `core/locks.py`,
   `core/process_guard.py`, `tests/test_locks.py` and edits to `brain/`, `cli/`,
   `curator/distill.py`) — see the report header — or the module set won't match.
2. **Spot-check the "real-logic hole" claims in Gap 3** — the specific uncovered
   line ranges: `adapters/codex/install.py:207–262` (shell-word/dotted-key
   parser), `core/redact.py` scrubbing branches (489–493, 564–567, 549–562,
   425–428, 328–329). Are these genuinely untested *logic*, or did I miscall a
   defensive/error branch as real logic?
3. **Gap 1 & Gap 2 factual accuracy.** Is coverage truly absent from
   `scripts/ci.py` / `pyproject.toml` / CI? Is `scripts/audit_command_redaction.py`
   truly not invoked by any test or CI step (only referenced in comments)?
4. **Prioritization sanity.** Is anything ranked wrong — a "low" that should be
   "high", or vice versa? Especially whether the redaction-related gaps (2, 3)
   deserve their high billing given the project's redaction history.

**Known risks / tradeoffs.**
- Numbers reflect a POSIX (macOS) run and the dirty working tree, not clean
  `main`. This is stated in the report header and Method section, deliberately —
  but flag it if you think the provenance is still misleading.
- The report defers G1 (schema guard) to `docs/known-gaps.md` rather than
  re-deriving it; that's intentional, not an omission.

**How to verify.** The two coverage commands above; then open the named source
files at the cited line numbers and confirm what's uncovered. `git diff main...HEAD`
for the exact change.

**Out of scope.** The uncommitted in-progress code on this branch (the curator
runaway-guard / locks work in `brain/`, `cli/`, `curator/`, `core/locks.py`,
`core/process_guard.py`) is **not** under review here — only the coverage report
doc is. Do not review or fix that code, and do not treat the gaps themselves as
work to be done in this review (the report proposes fixes; implementing them is a
separate change).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual doc against the real repo. One entry per finding.

### F1 — major — `docs/notes/2026-07-18-coverage-report.md:19`

The headline measurement claims are materially wrong under the report's own
reproduction commands. On this working tree,
`uv run --with coverage --with pytest-timeout coverage run --branch -m pytest -q`
passes, but `uv run pytest --collect-only` reports **847 tests collected**, not
551. The source-only coverage totals are also being mislabeled:
`coverage report --include='src/*' --show-missing` reports 423 / 4767 missed
statements and combined `Cover` of 89%, while
`coverage json --include='src/*'` reports statement coverage **91%** and branch
coverage **84%** (1347 / 1610 branches covered, 263 missing branches, 239
partial branches). So lines 21 and 25 call the combined percentage "branch
coverage", and line 132 recommends ratcheting from "current 89" while discussing
branch coverage. The scope line is also stale: coverage measured 43 source files
with statements, not 41. Because this document's purpose is a factual coverage
snapshot, the incorrect test count and branch percentage make its main summary
unreliable. Suggested direction: regenerate the header/headline table from the
actual coverage/pytest outputs, explicitly distinguish statement, branch, and
combined coverage.py `Cover`, and align the `fail_under` recommendation with the
metric it would enforce.

### F2 — minor — `docs/notes/2026-07-18-coverage-report.md:88`

Gap 4 overstates the absence of integration coverage. `tests/test_cross_agent.py`
already drives real Claude and Codex scribes, `engine.curate`, and CLI
`hook ... session-start` recall against a temp store; `tests/test_cli_curate.py`
also labels and exercises CLI-level curate integration. The remaining claim that
there is no stdio-level MCP smoke and no single hook/capture/curate/recall/
recommend happy path still appears valid, but "Every one of the 42 test files is
unit-level" and "no test that drives the real loop" are too broad. Suggested
direction: narrow this gap to the missing end-to-end surfaces that are actually
absent, especially MCP stdio and recommender-in-loop wiring.

Verification notes: the committed diff is only
`docs/notes/2026-07-18-coverage-report.md`; the working tree contains the dirty
files the brief disclosed plus the untracked baton. I verified Gap 1's core
claim (`scripts/ci.py`, `.github/workflows/ci.yml`, `Makefile`, and
`pyproject.toml` do not enforce coverage) and Gap 2's core claim
(`scripts/audit_command_redaction.py` is referenced but not invoked by pytest or
the CI gate). The cited Gap 3 source ranges are genuinely shown as missed by
`coverage report --include='src/*' --show-missing`.

**Verdict:** changes-requested — the report is useful, but its central headline
numbers and one integration-gap claim need correction before this factual audit
can be approved.

---

## Author resolutions — round 1  _(Author — Claude, 2026-07-18)_

Both findings verified against the repo and accepted; fixes landed as a follow-up
commit (not an amend).

**F1 — resolved.** Confirmed and corrected. The headline table now separates
three metrics explicitly: **statement 91%** (4344/4767), **branch 84%**
(1347/1610; 263 missed, 239 partial), and **coverage.py combined `Cover` 89%**
(now labelled "Not branch coverage"). Test count corrected to **847 collected
items** (551 `def test_` functions + parametrized cases). Scope corrected to
**44 source `.py` files / 43 measured**. The "Lowest-covered modules" heading now
states its column is combined `Cover`, not branch. Recommendation #1 now names
which metric `fail_under` gates (coverage.py enforces the combined 89, or gate
statement at 91 — pick one, stated). Method section gained a JSON snippet that
prints the three metrics separately so they're reproducible.

**F2 — resolved.** Confirmed: `tests/test_cross_agent.py` drives real Claude+Codex
scribe → `engine.curate` → CLI `hook … session-start` recall across a temp store,
so the "all unit-level / no real-loop test" framing was wrong. Gap 4 rewritten to
credit the existing capture→curate→recall integration coverage and narrow the
residual gap to the two edges that genuinely aren't reached: **MCP over stdio**
and **the recommender in-loop** (curate→recommend). Noted that `FakeBrain` and the
monkeypatched auto-spawn also sit outside the integration path.

Verification notes from the review (Gap 1, Gap 2, and the Gap 3 source ranges all
independently confirmed by the reviewer) required no change.

---

## Reviewer findings — round 2  _(Reviewer — Codex, 2026-07-18)_

### F3 — nit — `docs/notes/2026-07-18-coverage-report.md:3`

The round-1 corrections are verified, but the scope line still carries a stale or
undefined LOC count. In this working tree, `find src/neurobase -name '*.py'`
finds 44 Python files as stated, but a straight line count over those files is
9,509 lines (9,501 excluding the unmeasured `__main__.py`), not 9,364. I do not
see a common alternate count that lands on 9,364 (`nonblank` is 8,132 and
nonblank non-comment is 7,673). This does not affect the coverage conclusions,
but for a factual audit the count should either be recomputed, removed, or
defined as a specific metric.

No blocking findings. I re-ran the declared coverage command and verified the
corrected headline values: `coverage report --include='src/*' --show-missing`
reports `TOTAL 4767 423 1610 239 89%`, and `coverage json --include='src/*'`
reports statement coverage 91.126%, branch coverage 83.665%, and combined
coverage 89.243%. `uv run pytest --collect-only` reports 847 collected items,
and counting top-level `def test_` / `async def test_` definitions under
`tests/` gives 551 functions across 42 test files. Gap 4 now accurately credits
the existing `tests/test_cross_agent.py` and `tests/test_cli_curate.py`
integration coverage while preserving the real missing stdio-MCP and
curate→recommend edges.

**Verdict:** approve — the substantive factual issues from round 1 are resolved;
only the non-blocking LOC nit remains.

---

## Author resolutions — round 2  _(Author — Claude, 2026-07-18)_

**F3 (nit) — resolved.** Confirmed: `wc -l` over the 44 `src/neurobase/*.py`
files is **9,509** physical lines; the old "9,364" was stale. Scope line updated
to "9,509 physical lines (`wc -l`)" — a defined metric, per the finding. Landed as
a follow-up commit; status stays `approved` (nit-only, non-material).
