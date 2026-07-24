---
slug: store-write-lock-v2
status: awaiting-review
author: claude
reviewer: codex
branch: feat/store-write-lock-v2
diff: git diff main...feat/store-write-lock-v2
created: 2026-07-24
round: 1
---

# Review: per-project store write lock (v2) — ADR-0023, resolves G4

## Brief  _(Author — Claude)_

**Intent.** Nothing serializes mutations to a project's store. `SessionStart`
launches a **detached `curate --if-stale`**, and the staleness gate and the pass
are two separate steps, so a second launch can pass the gate while the first has
not yet consumed its raws. `upsert_curated`, `soft_delete_curated`,
`mark_consumed` and `prune_tombstones` therefore run concurrently against the
same files — as do the seed importer, MCP `memory_remember`, and both scribes.
This branch introduces a **per-project advisory write lock** that every mutating
pass holds for its whole duration. Readers never take it.

**This is a re-derivation, and the prior approval does not carry.** An earlier
`feat/store-write-lock-adr` took the same decision through **eight** Codex rounds
to APPROVED — against the pre-`StoreHandle` store API, and it is now 18 ahead /
116 behind `main`. Rather than reconcile that, the decision was re-implemented on
current `main`, the same call made for provenance Slice B (ADR-0022). ADR
renumbered **0017 → 0023** because origin claimed 0017 for the egress-policy
gate. The retired branch stays unmerged as the content record; its baton was
deliberately **not** ported. **This is round 1 of a fresh review.**

**Scope.** Branch `feat/store-write-lock-v2`, `git diff
main...feat/store-write-lock-v2` — one commit over `main` (`9ab8a95`). Key files:

- `src/neurobase/core/lock.py` — **new**. `project_lock` (context manager) and
  `write_raw_guarded` (the scribe capture path).
- `src/neurobase/core/store.py` — `Document.digest` + `content_digest`;
  microsecond `raw_filename`; `canonical_raw_name`; `parse_captured_at`;
  `_claim_fresh_raw_path` (generation suffix); `write_raw(unique=)`; `list_raw`
  ordered by authoritative `captured_at`; `mark_consumed(expect_digest=)`.
- `src/neurobase/core/store_handle.py` — `unique=` and `expect_digest=`
  passthroughs. `mark_consumed` now returns `Path | None`.
- `src/neurobase/curator/engine.py` — `curate` is now a thin locked wrapper over
  `_curate_unlocked` (which takes the handle); digest guard at step 6.
- `src/neurobase/adapters/{claude,codex}/scribe.py`, `cli/__init__.py`,
  `recommender/seed.py`, `mcp/server.py` — call sites.
- `tests/test_store_lock.py` (new, 11 tests), `tests/test_raw_consume_cas.py`
  (new, 18 tests), plus two existing assertions updated for the microsecond
  filename.

**Focus areas.** In priority order:

1. **The port surface — this is the only genuinely new code.** The *decision*
   survived eight rounds; the wiring against `StoreHandle` did not exist before.
   Read as fresh code:
   - **`curate`'s split into a locked wrapper + `_curate_unlocked(handle, …)`.**
     `root` is gone from the inner body; the handle is opened once in the wrapper
     and threaded down. Is anything now reached from the wrong root, or opened
     twice? Note `distill_docs` still takes a raw root and is passed
     `handle.root`.
   - **`write_raw_guarded(handle, project, **kwargs)`** — the biggest API change
     from the retired branch. It takes a `StoreHandle` rather than
     `(root, project)`. Are both scribes passing a **WRITE** handle, and does the
     contended fallback still go through the chokepoint?
   - **`mark_consumed` now returns `Path | None`.** Every caller must treat
     `None` as "not consumed". Is there any caller that ignores it?
2. **The lock's API shape — a deliberate divergence from the retired branch.**
   `project_lock` takes the project's **memory directory**, not `(root,
   project)`. Rationale: resolving `memory_dir(project, root)` inside `core.lock`
   would be a second, unvalidated way to name a store path under ADR-0015, and
   would have needed a `check_store_chokepoint.py` exemption. The guard passes
   with **no new exemption** — please confirm that is genuinely true and not an
   accident of what the guard matches. If you think the exemption would have been
   the more honest design, say so.
3. **Is the lock actually held everywhere it must be?** The claim is: curator,
   seed importer, and MCP `memory_remember` are the complete set of mutating
   passes. Please look for a mutator that still writes unlocked — that is the
   G4-closure claim, and a miss is a P1.
4. **`prune_tombstones` TOCTOU.** The claim is that it closes *structurally*
   because its only call site is inside the locked pass. Verify that call-site
   claim across the whole repo.
5. **Test vacuity — the highest-yield target on this branch.** See "Known risks"
   below: I already found one vacuous test in the **review-approved** inherited
   suite by mutation. There may be more. For each new guard, work out what
   mutation would kill its test; if none would, that is a `test-gap` finding.
6. **The raw-ordering rework.** `list_raw` now sorts by `captured_at` with the
   filename as tiebreak; filenames carry microseconds; same-instant collisions
   get a `__gNNNN` suffix and **`captured_at` is never advanced**. The spec now
   states the round-trip MUST on the *timestamp portion* only. Is that contract
   stated correctly and enforced?

**Known risks / tradeoffs.**

- **A review-approved inherited test was vacuous — found by mutation while
  porting.** Dropping `unique=True` from `write_raw_guarded`'s contended branch
  left the **entire** inherited suite green, including the tests written
  specifically for contended capture. The reason: that branch also restamps
  `captured_at=now()`, and at microsecond resolution that alone yields an unused
  filename, so the `unique` flag never did any work in any existing test. It only
  bites on a same-microsecond collision, which nothing constructed. Closed by
  `test_contended_capture_claims_a_fresh_name_on_a_same_instant_collision`, which
  freezes the clock; it fails without `unique=True`. **Assume there are others.**
- **`filelock` soft-lock fallback.** Where the platform primitive is unavailable
  (e.g. `flock` → `ENOSYS` on some network filesystems) `filelock` degrades to a
  soft lock, which does **not** auto-release on process death. The
  killed-holder guarantee is therefore conditional, and that is written into
  known-gaps G4's `residual:` rather than papered over.
- **The lock is advisory.** A writer bypassing `core.lock` is not excluded. That
  is exactly why `mark_consumed`'s `expect_digest` is retained — as
  defense-in-depth, explicitly **not** a compare-and-swap. An earlier revision
  claimed it was a CAS and Codex disproved that by injecting a write between the
  check and the write; the docstring now says so.
- **`_curate_unlocked` is unguarded by construction.** Nothing stops a future
  caller invoking it directly and losing the lock. The docstring says "nothing
  else may call this" — a convention, not an enforcement. Flag it if you think it
  needs teeth.
- **ADR-0023 is `Proposed`.** Deliberate; accepted ADRs are immutable here.

**Collateral change (disclosed).** `_existing_seed_state` now takes the caller's
`StoreHandle` instead of a `root`. It previously re-opened a READ handle **per
source file**, re-running the schema guard each time; the locked refactor left it
without a `root` in scope, and threading the handle was the natural fix.

**How to verify.**

```sh
git rev-parse HEAD                       # expect the tip SHA in the prompt
git diff main...feat/store-write-lock-v2
make ci                                  # last run: 1381 passed, 1 skipped, 91.97% cov
uv run pytest tests/test_store_lock.py tests/test_raw_consume_cas.py
uv run python scripts/check_store_chokepoint.py
```

`tests/test_store_lock.py` spawns real processes (`multiprocessing`, spawn
context) — cross-process serialization is the property under test and threads
cannot demonstrate it. It includes a deliberately-unlocked control that must
**lose** updates; if that control ever passes at the exact total, the probe
proves nothing.

**Out of scope.**

- The retired `feat/store-write-lock-adr` @ `e64399c` and its baton. Do not diff
  against it or treat its approval as carrying.
- Provenance Slice A / `core/graph.py`.
- Anything already on `main`, including ADR-0022's fold journal — except where
  this branch touches it (the digest guard's interaction with `fold.consumed` IS
  in scope).
- `STORE_SCHEMA_VERSION`: deliberately not bumped. The new frontmatter keys are
  additive and old binaries ignore them; the retired branch's reviewer concurred.
  Re-raise only if you disagree on the merits.

---

## Reviewer findings  _(Reviewer — Codex)_

> _Awaiting round 1._
