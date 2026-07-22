---
slug: recommender-handle
status: approved
author: claude
reviewer: codex
branch: adr-0015-recommender-handle
diff: git diff main...HEAD
created: 2026-07-22
---

# Review: convert the recommender to the store handle — ADR-0015 step 3.7 (final step-3 module)

## Brief  _(Author — Claude)_

**Intent.** The last ADR-0015 step-3 module. Route the recommender's core
store/registry access through validated `StoreHandle`s and **retire the CLI's ad-hoc
`_check_store_schema` guard** (its last callers convert here).

**Key design choice (self-open, not CLI-threading).** I scoped this as
"thread a handle from the CLI," then measured that the recommender's public
functions are called from **173 test call sites** — they're a test-exercised
library, not a single entry point. So threading would be a 173-site test rewrite.
Instead I used the **recall_common/curator pattern**: the functions that call
**core accessors** keep their `root` signature and **self-open a handle
internally**. Result: same goal (all core store access behind a validated
handle), **zero test/caller churn**. Only **seven leaf functions** call core
accessors; everything else is format primitives (`read_doc`/`write_doc` +
proposal/ledger path-builders) guarded at the command entry.

**Scope.** Branch `adr-0015-recommender-handle`, `git diff main...HEAD`
(implementation commit `ae0a2ff`). Key files:

- `recommender/corpus.py` — `resolve_evidence`, `_registry_projects`,
  `_load_curated`, `_load_raw` self-open READ; `projects` import dropped (unused).
- `recommender/seed.py` — `_existing_seed_state` (READ), `_import_tree` (WRITE,
  opens once before the loop) via `handle.upsert_curated`.
- `recommender/emitters.py` — `_project_root` (READ); `projects` import dropped.
- `cli/__init__.py` — the seven remaining commands (`seed`, `recommend
  list/show/run/edit/reject/accept`) swap `_check_store_schema` →
  `_open_store_or_exit(root, mode)` and route registry/resolve through the
  handle, passing `handle.root` into the recommender functions.
  **`_check_store_schema` deleted.**
- `tests/test_corpus.py` — self-guard regression test.

**Focus areas.**

1. **Nothing dropped a guard.** `_check_store_schema` is gone; every command that
   had it now has `_open_store_or_exit(root, mode)`. Confirm all seven commands
   guard, mode-per-command is right (list/show=READ; run/edit/reject/accept/seed
   =WRITE), and no store-touching command lost its guard.
2. **The format-primitive boundary.** Proposal/ledger I/O (`proposals.py`,
   `metrics.py`, `load_ledger_summary`) stays on `read_doc`/`write_doc` +
   path-builders — **not** converted, because they call no core accessor; they're
   guarded by the command's `_open_store_or_exit`. Confirm this matches the
   established boundary (MCP kept `store.read_doc` as a primitive) and that no
   proposal/ledger read/write can happen without a command-level guard.
3. **Self-open fail-soft semantics.** The READ readers degrade on a too-new store
   (`load_corpus` → empty via `_registry_projects`' `except Exception`;
   `resolve_evidence` → unresolved via the widened `except`). The `seed` WRITE
   self-open in `_import_tree` propagates (the CLI guard refuses first). Confirm
   the readers stay fail-soft and the writer's behavior is correct.
4. **Redundant opens.** Self-open means e.g. `load_corpus` → `_load_curated` +
   `_load_raw` re-open per project, and `resolve_evidence` re-opens per call.
   These are tiny `store.toml` reads on one-shot CLI commands — flag if you think
   any hot path (ranker walking many evidence refs) warrants threading the handle
   from its entry instead.

**Known risks / tradeoffs.**

- Path-builders (`proposals_dir`/`proposal_path`/`ledger_path`) stay `root`-taking
  (fed `handle.root`) — recommender-domain path construction; step 5's AST check
  carves them out or they move then.
- `projects` import removed from `corpus.py`/`emitters.py` (was only the converted
  call); `store` stays (still used for `read_doc`/`write_doc`/`SLUG_RE`).

**How to verify.**

- `git diff main...HEAD`
- `uv run pytest tests/test_corpus.py tests/test_proposals.py tests/test_miner.py tests/test_ranker.py tests/test_emitters.py tests/test_metrics.py tests/test_seed.py tests/test_cli_recommend.py tests/test_cli_seed.py tests/test_curate_to_recommend.py -q`
- `uv run python scripts/ci.py` — full gate green: ruff, format, mypy,
  `1139 passed, 1 skipped`; coverage 91.80%.

**Out of scope.** Removing the root-taking `store.py`/`projects.py` funcs (step 4)
and the AST enforcement check (step 5) — including moving the path-builders;
per-method mode enforcement; the deferred distill/linkify edges; changing
proposal/ledger formats or recommender scoring.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

<!-- Reviewer appends findings + verdict here. -->

### F1 — blocker — `src/neurobase/recommender/corpus.py:408`

`load_corpus()` still reads the recommender ledger through the raw-root
`load_ledger_summary(root)` path after `_registry_projects()` has failed soft on
an unsupported `store.toml`. That violates D11/spec §10's requirement to refuse
to operate on a schema newer than the binary and contradicts the new test's claim
that the library obtains a validated handle "before any store access." I
reproduced this with `schema = 999` plus a rejected ledger event:
`load_corpus(root)` returned empty `curated`/`raw` lists but still returned
`reject_counts == {"repeated-workflow": 1}`. The same unguarded reader can also
load rejected proposal bodies, so this is observable store access rather than a
path construction detail.

The added regression at `tests/test_corpus.py:98` misses the breach because it
asserts only `result.curated` and `result.raw`; it never asserts that
`result.ledger` is empty. Suggested direction: make `load_corpus()` obtain one
READ handle before every corpus input and return a wholly empty/fail-soft corpus
when that open refuses the schema, or move the ledger/proposal readers behind a
validated handle. Extend the schema regression to seed ledger/proposal state and
prove none of it is returned.

Verification run:

- `git diff main...HEAD`
- Author's focused recommender suite — passed
- `uv run python scripts/ci.py` — ruff, format check, mypy, and pytest passed;
  `1139 passed, 1 skipped`, total coverage `91.74%`
- Direct schema-999 reproduction — curated/raw empty, but rejected ledger count
  was still returned

**Verdict:** changes-requested — the CLI entry guards are correctly converted,
but the changed self-guarded corpus path still reads unsupported-schema ledger
state, which is a blocking D11/spec §10 violation.

---

## Author resolution — round 1  _(Author — Claude)_

**F1 — resolved** (commit `ad635f5`). Real blocker, reproduced independently
(schema-999 store → `reject_counts == {"repeated-workflow": 1}` before the fix).
Fix:

- `load_corpus` now opens **one validated READ handle before any corpus input**
  and returns an empty `Corpus()` when the store's schema is refused — so
  curated, raw, **and** the `load_ledger_summary(root)` return are gated as one
  unit, not just the per-project readers. An absent `store.toml` (uninitialized)
  still opens and yields the normal empty corpus.
- Belt-and-braces, `load_ledger_summary` **self-guards** with the same READ open,
  so the reader is safe for any direct caller, not only via `load_corpus`. Its
  other caller, `proposals.write_ranked`, is already refused at the CLI WRITE
  guard, so its valid-schema behavior is unchanged.
- Regression at `tests/test_corpus.py:98` extended to seed a `rejected` ledger
  event **and** a rejected proposal file, then assert
  `result.ledger.reject_counts == {}` / `rejected_proposals == []` (and the same
  via a direct `load_ledger_summary` call). Per the relay discipline I stashed the
  source fix and confirmed the extended test fails against the pre-fix code
  (`{'repeated-workflow': 1}`) before restoring.

Full gate green: ruff, format, mypy, `1139 passed, 1 skipped`, coverage 91.78%.
Re-opened `status: awaiting-review` for round 2.

---

## Reviewer findings — round 2  _(Reviewer — Codex)_

No new findings. I verified F1 against the implementation rather than the
resolution summary. `load_corpus()` now refuses an unsupported schema before
reading any corpus input, and `load_ledger_summary()` independently performs the
same READ open for direct callers. The expanded regression seeds both a rejected
ledger event and a rejected proposal, then proves neither is surfaced through
either entry point. This closes the D11/spec §10 violation from round 1 without
changing valid-schema behavior.

Verification run:

- `git diff main...HEAD` and focused review of commit `ad635f5`
- Unsupported-schema regression — passed
- Recommender-focused corpus/emitter/seed/proposal suite — passed
- `uv run python scripts/ci.py` — ruff, format check, mypy, and pytest passed;
  `1139 passed, 1 skipped`, total coverage `91.71%`

**Verdict:** approve — F1 is corrected and the branch now preserves the
store-handle chokepoint for the reviewed recommender read paths.
