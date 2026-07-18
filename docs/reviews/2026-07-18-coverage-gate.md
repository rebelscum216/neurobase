---
slug: coverage-gate
status: awaiting-review
author: claude
reviewer: codex
branch: wire-coverage-gate
diff: git diff a65f86f...HEAD
created: 2026-07-18
---

# Review: wire coverage into the gate + close the reported coverage gaps

## Brief  _(Author — Claude)_

**Intent.** Implement recommendations **1–4** of the approved coverage report
([`docs/notes/2026-07-18-coverage-report.md`](../notes/2026-07-18-coverage-report.md),
verdict `approved` in [`2026-07-18-coverage-report.md`](2026-07-18-coverage-report.md)).
That report was a *factual audit*; this branch is the *work it proposed*. G1 (the
store-schema guard) is explicitly **not** touched — it stays deferred to its own ADR
per the report's recommendation #5.

**Getting a clean checkout.** `main`'s working tree currently carries unrelated
in-progress work (curator runaway-guard / `core/locks.py` / `core/process_guard.py`
plus edits to `brain/`, `cli/`, `curator/`, and several tests, **including
`tests/test_redact.py`, which this branch also touches**). A plain
`git checkout wire-coverage-gate` will therefore conflict. Use a worktree:

```bash
git worktree add /tmp/nb-review wire-coverage-gate && cd /tmp/nb-review
```

**Scope.** Branch `wire-coverage-gate`, base `a65f86f`, `git diff a65f86f...HEAD`.
Four commits, one per recommendation. **Use the `a65f86f...HEAD` range, not
`main...HEAD`** — the branch is based on the coverage-report docs commits, which you
already reviewed and approved; `main...HEAD` would replay those 375 lines as noise.

| Commit | Rec | Files |
|---|---|---|
| `e0d2e1f` | #2 | `scripts/audit_command_redaction.py`, `tests/test_redact_audit.py` (new) |
| `018ae17` | #3 | `tests/test_codex_install.py`, `tests/test_redact.py` |
| `d82a972` | #4 | `tests/test_mcp_stdio.py` (new), `tests/test_curate_to_recommend.py` (new) |
| `cc41746` | #1 | `pyproject.toml`, `uv.lock`, `scripts/ci.py` + 6 doc mirrors |

**No file under `src/` is modified by this branch.** It is config, scripts, tests
and docs only. `src/neurobase/core/redact.py` in particular is byte-identical to
`main`.

**Focus areas.** Where I most want your eyes:

1. **The `fail_under` value and what it gates (`pyproject.toml`).** This is the
   judgement call most likely to be wrong. Three things to check:
   - I claim coverage.py's `fail_under` enforces the **combined `Cover`**, not
     branch and not statement coverage. Verify that claim.
   - I seeded `90` from a measured floor, and the number compared is the **raw**
     percentage, not the rounded one `coverage report` prints — `TOTAL` displays
     `89%` at `88.57` and pytest-cov still fails it. Confirm.
   - **Is 90 too tight?** Measured: macOS py3.13 **90.33**, py3.11 **90.33**,
     Windows **90.17** (*simulated* by deselecting the four `skipif(win32)` seed
     tests — I cannot run a real Windows runner). That leaves ~0.17 margin on the
     tightest cell. If you think a real Windows runner will land below 90, say so —
     I would rather drop to 89 now than turn 2 of 6 matrix cells red on push.
2. **Rec #2's central decision.** The report offered "add the script to the gate
   **or** fold it into pytest". I chose fold-into-pytest and argue that adding
   `scripts/audit_command_redaction.py` to `scripts/ci.py` would be actively
   *worse* than nothing: its corpus is `~/.claude/projects`, which no CI runner
   has, so it would print "nothing to audit", return 0, and give false assurance on
   every push. `test_main_returns_zero_when_there_is_no_corpus` pins exactly that.
   **Is that reasoning right?** If you disagree, this is the finding to write.
3. **Did I actually add signal, or just move lines?** `test_redact.py` already
   asserted idempotence per sample. I claim the genuinely new assertion is
   `changed_without_marker == 0`, asserted nowhere in pytest before. Check whether
   `tests/test_redact_audit.py` earns its 383 lines or is largely ceremony.
4. **Test strength over test count.** The suite went 847 → 956 collected. The
   failure mode I most want caught is a test that *executes* a line without
   *constraining* it. Specifically worth attacking:
   - `test_curate_to_recommend.py` asserts `breadth == 6` and that the miner's
     deliberately-absurd self-reported counts (`99`, `"everyone"`, `"nowhere"`) do
     not survive. Is that genuinely only satisfiable if data crossed every stage,
     or can it pass against a mis-wire?
   - The `ANSI_C_UNDECODABLE` fail-closed cases in `test_redact.py` ship with a
     decodable control set precisely so "redact everything" wouldn't pass. Is the
     control adequate?
   - `test_codex_install.py` pins `_ESCAPES` against the TOML 1.0 spec rather than
     against the implementation. Did I get the spec right?
5. **`test_mcp_stdio.py` as a CI liability.** It spawns a real subprocess, and this
   repo has a documented runaway incident plus a 60s pytest-timeout guard. I bounded
   every wait and reap the child in a `finally`. Hunt for any path that could hang
   or flake, especially on Windows.

**Known risks / tradeoffs.**

- **The Windows floor is simulated, not measured.** Stated above; it is the single
  weakest empirical claim in this branch.
- **`source = ["src/neurobase"]` changes the denominator** vs the report's
  `--include='src/*'`: it pulls in `__main__.py`, which is never imported, taking
  the combined figure 88.65 → 88.57 on the same code. That is honest (a file with
  no tests *should* show) but it means this branch's numbers are not directly
  comparable to the report's. I did not add an `omit` for it — argue if you disagree.
- **`test_mcp_stdio.py` imports `anyio` directly**, which is a transitive dependency
  of `mcp` rather than a declared one. `mcp` is pinned (`==1.28.1`) so it is stable,
  but it is a latent coupling. I judged adding a dev dependency worse; say if not.
- **`test_redact_audit.py` mutates `sys.path`** at import to reach `scripts/`, which
  is not a package. The alternative was duplicating the property into the test file,
  which would let the two drift. I took the import.
- **`_EXPECTED_TOOLS` pins the MCP tool list exactly**, so adding a tool fails that
  test. Deliberate (forces intentionality), but it is a maintenance cost.
- The four commits are ordered so **every commit is individually green** — the tests
  must exist before a 90% floor can be enforced. This means rec #1 lands *last*
  despite being recommendation #1. Verified, not assumed (see below).

**How to verify.**

```bash
git worktree add /tmp/nb-review wire-coverage-gate && cd /tmp/nb-review
git diff a65f86f...HEAD

uv run python scripts/ci.py          # the full gate — expect green, 956 passed, 1 skipped

# the coverage claims, reproduced:
uv run pytest --cov=src/neurobase --cov-branch --cov-report=term-missing -q | tail -3

# the per-commit-green claim:
for c in e0d2e1f 018ae17 d82a972 cc41746; do
  git checkout -q $c && uv run python scripts/ci.py 2>&1 | tail -1
done; git checkout -q wire-coverage-gate

# the simulated Windows floor:
uv run pytest --cov=src/neurobase --cov-branch -q \
  --deselect tests/test_cli_seed.py::test_seed_unreadable_from_dir_target_is_hard_cli_error \
  --deselect tests/test_seed.py::test_unreadable_top_level_directory_is_a_hard_error \
  --deselect tests/test_seed.py::test_unreadable_file_is_skipped_but_run_continues \
  --deselect tests/test_seed.py::test_symlinked_file_is_skipped_not_followed | tail -2
```

The skipped test is `test_real_transcript_corpus_holds_the_invariants` — opt-in
behind `NEUROBASE_AUDIT_REAL_TRANSCRIPTS=1`, by design.

Reported module movement, worth re-measuring rather than trusting:
`adapters/codex/install.py` 82 → 93, `core/redact.py` 88 → 97, `mcp/server.py`
85 → 86 (line 247, `serve()`), total combined 88.57 → 90.33.

**Provenance you should know about.** Most of these tests were drafted by parallel
subagents. The adversarial verification pass I had planned for each one **died on a
session limit and never ran**, so I verified the branch myself: scope (no `src/`
changes), the exact-output assertions, the presence of controls, per-commit green,
and I independently re-derived the four bash-semantics claims in `test_redact.py`'s
new `EXACT` rows against real `bash`. That is author self-verification, not
independent review — which makes *your* pass the only independent one this branch
has had. Please weight it accordingly, especially focus areas 3 and 4.

**Out of scope.**

- **G1 / the store-schema guard.** Deferred to its own ADR by the report's rec #5.
  Do not treat its absence as a finding.
- **The uncommitted runaway-guard work on `main`'s working tree** (`core/locks.py`,
  `core/process_guard.py`, `brain/`, `cli/`, `curator/distill.py`). Not on this
  branch, not under review, and deliberately not swept into these commits.
- **The coverage report and its own review** (`docs/notes/2026-07-18-coverage-report.md`,
  `docs/reviews/2026-07-18-coverage-report.md`). Already reviewed and approved by
  you; they are only in `main...HEAD` because they are this branch's base. Use the
  `a65f86f...HEAD` range and they disappear.
- **Chasing the remaining uncovered lines.** The report is explicit that the residual
  misses are defensive `except`/guard arms and that driving them to 100% is low
  value. `recommender/metrics.py` (80%) is the lowest module and stays that way.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

_(awaiting review)_
