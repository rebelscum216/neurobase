---
slug: route-memory-layering-s11
status: approved
author: claude
reviewer: codex
branch: docs/route-memory-layering-s11
diff: git diff main...HEAD
created: 2026-08-04
rounds: 3
last_reviewed: 632ad48f13ad527afa568cf195609e90288a1785
---

# Review: routing §11 of the memory-layering note into ADR-0027, the Backlog, and `G12`

> **Status note (2026-08-04).** Three rounds ran. The Brief describes the branch at
> `d7c7a99`; rounds 2 and 3 reviewed `bbed022` and `632ad48`, and one optional nit
> landed afterward as `2c40576`. The Brief is left as written — it is the record of
> what was handed over — and the outcome is at the end.

## Brief  _(Author — Claude)_

**Intent.** A 2026-08-04 measurement established that retroactive transcript distill is
*structurally* cache-hostile: 959,550 cache-creation tokens at a **0.44%** cache hit
rate, because distill chunks a rendered transcript that later passes do not re-read.
That produced instrumentation (PR #17) and a reproducible harness (PR #18) but **no
scheduled work**, because the analysis lived in §11 of a working note that
`docs/notes/README.md` says "carries no authority." This routes the load-bearing parts
into homes that can schedule against them.

**Scope.** Branch `docs/route-memory-layering-s11`, `git diff main...HEAD`. Docs only:

- `docs/adr/0027-authored-raw-authority-field.md` — NEW. D45–D48: a raw MAY carry
  `authority`; absent means `captured`. Lands on **schema 1**. D47 gives the field zero
  behavioral power; D48 keeps D13 redaction unconditional.
- `docs/neurobase-build-plan.md` §6 — two Backlog items: the authored-body ingestion
  path, and auto-tier sizing/admission control.
- `docs/known-gaps.md` — `G12`.
- `docs/adr/README.md` — the 0027 index row.

**Focus areas.** Whether the code claims are *true* rather than merely cited; whether
each artifact is routed per its own file's stated rule; whether D46's schema-1 reasoning
survives contact with ADR-0016; whether D47's "zero behavioral power" is enforceable or
just a promise.

**Known risks / tradeoffs.** The headline measurements come from a local store not in
this repo and are **not independently verifiable here** — the reviewer was told so and
asked not to pretend otherwise. The source note is unpublished, so its section
references are provenance for the author, not inspectable by a public reader.

**How to verify.** Read the diff against the cited source at the review SHA; every
`file:line` was re-derived (the note's own citations were against `3642bd9` and had
drifted).

**Out of scope.** `G9`/`G11`, which predate this branch and belong to a closed review.

---

## Reviewer findings  _(Reviewer — Codex)_

**Round 1 (`d7c7a99`) — changes-requested.** 12 findings: 9×P2, 3×P3. No P0/P1.
**Round 2 (`bbed022`) — changes-requested.** 8 fixed, 4 partial, 1 new P3.
**Round 3 (`632ad48`) — approved with nits.** All 13 fixed; one optional P3.

Findings used the relay's `P<n>-<CATEGORY>-NNN` scheme. Summarised by what they changed:

### Four false claims caught, three from the same root cause

Three were **repo comments or docstrings quoted without verification** — the quote
laundered a wrong claim into a document carrying more authority than the comment did:

- **`core/config.py:55-57`** — *"no raw in this store chunks and each costs exactly one
  call."* False: `_chunk` takes the **rendered transcript**, not the raw body
  (`curator/distill.py:504`); ADR-0014 records a 0.62 MB session as 2 distill + 1 merge;
  and five paths spend **zero** distill calls (`no_pointer`, `transcript_gone`, cache
  hit, `unsupported_agent`, `empty_render`). **This became `G12` itself.**
- **`curator/engine.py:474-479`** — *"would break the hooks-always-exit-zero
  guarantee."* False for the hook path: `spawn_curate_if_stale` is detached
  (`start_new_session=True`, output to `DEVNULL`), so the child's exit code never
  reaches the hook. The real reason bounded exhaustion is not an error is that it is
  normal and retryable.
- **`core/store_handle.py`** — *"The D11 schema comparison lives here and only here."*
  Overstated: `ensure_store_metadata` (`core/store.py:105-125`) retains its own check.

The fourth was a **design correction**: `G12` twice claimed a budget-expired pass
"repeats indefinitely." It does not. `distill_docs` runs with `write_cache=not dry_run`
(`engine.py:422`) and `_cache_write` persists each digest *before* the first plan debit
(`distill.py:523-524`), so an expired pass leaves raws unconsumed **but digests cached**
— the next pass is cheaper and reaches planning. **Cache-warming, not a stall**
(`tests/test_distill.py:728-736`). Relatedly, `auto_max_raws = 40` is **volume-derived**
(`config.py:49-53`), not latency-derived.

### The convergence guard, and the split

`G12`'s central framing was wrong in round 1 ("the two ceilings cannot both be honored")
and wrong again in round 2 ("sized from an unmeasured latency estimate" + "repeats
indefinitely"). Rather than reframe a third time, the entry was **split**:

- **`G12`** keeps only what is unambiguously wrong now — the `auto_max_brain_calls`
  comment's cost model. It claims nothing about whether `50` is the wrong number and
  nothing about pass behavior.
- **The sizing / admission-control analysis moved to the build-plan Backlog**, where a
  not-yet-built improvement belongs.

Round 3 confirmed the routing: *"A false rationale in a shipped source-code comment
qualifies under `known-gaps.md`'s own 'wrong or inconsistent right now' bar: the comment
currently disagrees with the implementation and is the stated rationale for a shipped
default… Conversely, selecting, sizing, and admitting future auto-tier work are unbuilt
product/design choices, so the new Backlog item is their proper home."* It also confirmed
nothing was orphaned in the split, and that three rounds of correction had **not** hedged
the documents into asserting nothing.

### Other substantive outcomes

- **D47 was given a mechanism** rather than an assertion: a spec-appendix update, the
  `_raw_payload` seam (`curator/engine.py:123-124` passes **no** frontmatter into the
  plan payload), and paired fixtures that must fail if toggling only `authority` changes
  any planning-side input. Round 3: this *"would catch `authority` entering
  planner/ranker/recall/proposal-scoring inputs if implemented as written."*
- **Raw identity was restated** as the whole `(captured_at, agent, sid8(session_id))`
  tuple, with the lookup contract the ingestion verb must define — full session/agent
  identity, zero/one/many unconsumed matches, the `SessionEnd` race, already-consumed.
- **The `neurobase wrap` interface is marked unratified** in both the ADR and the
  Backlog; `wrap --summary -` carries no session or agent identity and is a candidate,
  not a contract.
- **Citation precision** was itself a recurring finding — two off-by-ones introduced by
  round-1 fixes, two more ranges stopping one line short of the symbol they
  substantiate. In artifacts whose value *is* citation precision, this is the failure
  mode to watch.

**Verdict:** **approve** — *approved with nits after three rounds; the one remaining P3
was citation precision, marked "None for approval" and applied anyway in `2c40576`.*

## Notes

- No test suite was run on either side: the change is markdown-only, nothing in `tests/`
  parses the docs tree, and the reviewer's sandbox blocked `uv`'s cache (it said so
  rather than claiming otherwise).
- The relay ran with the reviewer in a **detached-HEAD throwaway worktree** pinned to
  each review SHA, so a concurrent session could not drift `HEAD` mid-review. Before and
  after each round, branch/HEAD/local-refs/dirty-state were compared; the only diff was
  the two pre-existing untracked notes.
- `ADR-0027` was flipped `Proposed` → `Accepted` on this verdict.
