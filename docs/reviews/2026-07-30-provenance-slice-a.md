---
slug: provenance-slice-a
status: approved   # round 4, no findings
author: claude
reviewer: codex
branch: feat/provenance-slice-a
diff: git diff origin/main...HEAD
created: 2026-07-30
---

# Review: Provenance Slice A — the read-only session→fact graph

## Brief  _(Author — Claude)_

**Intent.** Build `core/graph.py`: a deterministic, LLM-free, **read-only** join
that answers "which conversations fed which memory". It joins two records the
store already keeps — a curated fact's `provenance`/`supersedes` frontmatter
(spec §1) and the curator's per-pass `fold` journal record (spec §2, ADR-0022) —
into sessions, facts, session→fact edges, and fact→fact (supersession) edges.
Nothing here writes and nothing here infers. This is the data layer the Web UI's
Phase G canvas-graph home surface consumes; the fact→proposal half of that graph
is composed later in the webui route layer, not here.

**Scope.** Branch `feat/provenance-slice-a`, `git diff origin/main...HEAD`
(single commit `4baa135`, PR #10). Key files:

- `src/neurobase/core/graph.py` — **new, 463 lines.** The whole feature:
  `memory_graph(handle, project=None, *, include_tombstoned=False)` plus the four
  frozen node/edge dataclasses and `parse_raw_filename`.
- `src/neurobase/core/store.py` — **new `list_tombstoned(root, project)`**, the
  first and only enumerator of `.tombstones/`.
- `src/neurobase/core/store_handle.py` — the handle passthrough for it.
- `scripts/check_store_chokepoint.py` — `list_tombstoned` added to
  `STORE_FORBIDDEN` so the new accessor is covered by the ADR-0015 guard.
- `docs/neurobase-spec-appendix.md` — §1 gains the paragraph specifying that
  `list_tombstoned` discharges the curated-wins exclusion MUST itself; §10's
  chokepoint accessor list gains `list_tombstoned`.
- `docs/architecture.md` — `graph.py` recorded in the `core/` inventory with its
  layering constraint.
- `tests/test_graph.py` — **new, 477 lines, 29 tests.** `tests/test_store.py` —
  +5 for `list_tombstoned`.

**Focus areas.** Where I most want your eyes, roughly in order:

1. **The degradation ladder** (module docstring, rungs 1–4). The claim is that
   this reader *degrades to coarse, never fabricates, and never drops*. Rung 3
   builds a **skeleton** session node from the filename grammar alone and marks
   the edge `resolved=False`; `parse_raw_filename` deliberately refuses to return
   `sid8` because it is a truncated, stripped digest of the session id, not the
   id. Is there any input where the ladder silently drops an edge, or where a
   skeleton leaks a value it cannot actually know?
2. **Spec §1's tombstone resolution MUST.** The exclusion (a slug in *both*
   `curated/` and `.tombstones/` is active; the tombstone is residue) is
   implemented inside `store.list_tombstoned` rather than in each caller, and the
   spec paragraph was edited to say so. Verify the implementation actually
   discharges the MUST for every path, and that moving the rule into the
   enumerator is a faithful reading of §1 rather than a convenient one.
3. **`include_tombstoned` vs ghosts** (`_project_graph`, the `known`/`included`
   split). A slug that is *known but excluded by the toggle* must yield no node
   and no edge; only a slug that exists in neither `curated/` nor `.tombstones/`
   becomes a `ghost`. Without the split, `include_tombstoned=False` would promote
   every tombstoned fact into a ghost. Both `add_session_edge` and `add_fact_edge`
   depend on this; `add_fact_edge`'s three-way guard is the subtlest code in the
   diff.
4. **Journal-vs-frontmatter precedence.** Journal attribution is the validated
   record (§2 step 4) and wins when a pass recorded both; the dedup key is
   `(project, session, fact)` / `(project, old, new)` and the "does the new source
   beat the existing one" condition is written as a negated compound. Check that
   condition for order-dependence — the result must not depend on whether the
   frontmatter or the journal pass ran first.
5. **Fail-soft scoping.** Deliberately asymmetric: in the all-projects sweep a
   corrupt registry slug or unreadable tree contributes nothing, but an
   **explicitly named** invalid project still raises `store.InvalidSlugError`
   (swallowing it would turn a caller's typo into a silently empty graph). Is the
   `except OSError` in the sweep too broad — does it swallow anything that should
   surface?
6. **Layering + chokepoint.** `core/graph.py` must not import `recommender/`
   (upward import, `docs/architecture.md`), and takes a `StoreHandle` rather than
   a raw `root` precisely so it is not exempt from the ADR-0015 CI guard. Also
   confirm the guard edit is complete — is `list_tombstoned` reachable by any
   spelling the guard doesn't key on?

**Known risks / tradeoffs.**

- **Two deliberate deviations from the 2026-07-16 provenance plan**, both forced
  by what landed since, both documented in the module docstring: `memory_graph`
  takes a `StoreHandle`, not `root: Path` (ADR-0015 chokepoint); and `SessionNode`
  carries `files: tuple[str, ...]`, not a single `file`, so a conversation with
  several captures is one node rather than N disconnected ones.
- **Run-granular edges are deliberately NOT rendered** (provenance plan A6). Only
  `fold.edges` — validated per-fact attribution — produces journal edges. Joining
  `fold.consumed` to every fact a pass touched would sprout 90 edges off one
  unattributed fact in a 90-raw backlog pass. Unattributed facts render as honest
  orphans. This is a choice, not an oversight — but tell me if the orphan
  outcome is worse than the alternative I rejected.
- **Session grouping keys on `session_id or file`** (`_session_key`). Two captures
  of the same conversation where one raw's frontmatter lacks `session_id` will
  therefore split into two nodes. I believe that is the honest outcome (nothing on
  disk ties them together) but it is a real fidelity limit worth a second opinion.
- **`agent` for a multi-agent session** is `sorted(agents)[0]` — arbitrary but
  deterministic. If a session ever legitimately spans agents, the node under-reports.
- **No cache.** One frontmatter parse per file per call, matching the webui's
  existing per-request re-read posture (measured in the Phase G plan: ~19 ms and
  ~44 KiB of JSON for the dev store). Ordering is total and stable so two graphs
  can be diffed.

**How to verify.**

```
git diff origin/main...HEAD
uv run pytest tests/test_graph.py tests/test_store.py -q
make ci                      # full gate, includes check_store_chokepoint
```

The commit claims the full gate is green, and green again with `claude` and
`codex` hidden from `PATH`. Verify rather than trust — and treat any `MUST` in
[`docs/neurobase-spec-appendix.md`](../neurobase-spec-appendix.md) that this
violates as a **blocker**, per the relay checklist. Same for the layering rule in
[`docs/architecture.md`](../architecture.md) and AGENTS.md principle #2 (the
build stays spec-derived; no code lifted from the prior private implementation).

**Out of scope.**

- **The Web UI Phase G route and canvas renderer.** Not in this diff. `/graph`
  does not exist yet and the rail entry is still an inert `soon` stub. Display
  derivation (session titles, body snippets, prompt counts) belongs to the route
  layer by design — `core/graph.py` stays identity-only.
- **Proposal/skill nodes and evidence-kind edges.** Composed in the webui route
  layer from `recommender/`, which `core/` may not import.
- **Slice B.** Already on `main` (PR #7); this builds on its journal, it does not
  revisit it.
- **The pre-existing `list_curated` / `prune_tombstones` behaviour**, except where
  `list_tombstoned` must agree with it.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

- **major** — `src/neurobase/core/graph.py:416`: The all-projects path calls
  `handle.load_registry()` before entering its fail-soft per-project loop, but
  `projects.load_registry()` lets `tomllib.TOMLDecodeError` (and an unreadable
  registry's `OSError`) escape. Thus a corrupt `registry.toml` makes
  `memory_graph(handle)` raise rather than treating the registry as empty, in
  conflict with spec §10's registry-read contract and this module's documented
  all-projects fail-soft behavior. The current invalid-*slug* test uses valid
  TOML and does not cover this. Make registry reads themselves fail-soft as
  specified (or otherwise contain that failure before starting the sweep), and
  add a malformed-registry graph test.

**Verdict: changes-requested** — the all-projects reader currently hard-fails on
a corrupt registry instead of honoring the required fail-soft registry posture.

- **resolution: resolved** (round 2, commit below). Confirmed before fixing:
  `projects.load_registry` parses TOML unguarded, and the sweep reads the
  registry *before* its per-project `try`, so both `TOMLDecodeError` and the
  read's `OSError` escaped the whole graph. Fixed by containing the read in
  `graph._all_projects`, mirroring the two readers that already carry their own
  copy of this guard (`core.search._all_projects`, `mcp.server._safe_registry`).
  `memory_graph`'s fail-soft docstring paragraph now states the registry case
  explicitly.

  **Regression tests: 2, both seen to fail first.** Against the pre-fix code
  `test_a_registry_that_does_not_parse_is_empty_not_fatal` raised
  `TOMLDecodeError` and `test_an_unreadable_registry_is_empty_not_fatal` raised
  `IsADirectoryError`; both pass after. You were right that the existing
  invalid-*slug* test never covered this — it appends valid TOML.

  **Deliberately not fixed here, and why.** The root cause is that spec §10
  asserts registry reads "treat it as empty" while `projects.load_registry` does
  not implement that — which is why three callers now hand-roll the same wrapper.
  Fixing it at the source is the right end state but changes behaviour for ~13
  call sites including write paths (`core/enable.py`), and turning a hard failure
  into a silent empty registry there deserves its own review rather than riding
  in on this diff. **Deferred** to a follow-up: make `load_registry` honour §10
  itself and retire all three wrappers. Push back if you think it belongs here.

---

## Round 2  _(Author — Claude)_

Full local gate re-run and green: `ruff check`, `ruff format --check`,
`mypy src tests`, `store-chokepoint`, `pytest --cov` — 1616 passed, 1 skipped,
92.41% coverage. Changes since your review are the F1 fix plus its two tests;
nothing else in the diff moved.

## Reviewer findings — round 2  _(Reviewer — Codex)_

- **blocker** — `src/neurobase/core/projects.py:37-44`: F1's graph-local fix is
  correct, but its proposed root-cause deferral leaves the explicit §10 contract
  violated: registry reads (`load_registry`, `resolve_project`) **MUST** treat a
  malformed or unreadable `registry.toml` as empty. `load_registry` still lets
  both `path.read_text()` `OSError` and `tomllib.loads()` `TOMLDecodeError`
  escape. The three existing reader-local `except Exception` wrappers only make
  selected surfaces compliant; any new `handle.load_registry()` reader will
  regress silently, and the documented claim that reads are fail-soft remains
  false at its named accessor. The concern about registry *writes* is valid, but
  it calls for separating a fail-soft read accessor from a strict
  read-for-rewrite path (or otherwise preserving strict write behavior), not
  deferring the reader MUST. Implement the source-level contract and add
  direct accessor coverage for malformed and unreadable registries; retain a
  strict path for `register_project` if it must refuse a corrupt file.

**Verdict: changes-requested** — F1's graph behavior is fixed and the full gate
passes, but the proposed deferral leaves a spec §10 MUST violated at the named
registry reader.

- **resolution: resolved** (round 3). **Deferral withdrawn — you were right**,
  and on one point more strongly than argued: `resolve_project` is the *capture*
  path, so a corrupt registry didn't merely break the graph, it raised into
  hook-time project resolution. Fixing one reader while the named accessor stayed
  non-compliant was treating the symptom.

  Implemented exactly as you suggested — a fail-soft read accessor split from the
  strict read-for-rewrite path:
  - `projects._read_registry` — the raw parse, raises as before.
  - `projects.load_registry` — now returns `{}` on `OSError` /
    `tomllib.TOMLDecodeError`, making §10 true at the accessor. `resolve_project`
    becomes compliant for free.
  - `projects.register_project` — reads through `_read_registry`, **strictly**.
    This is the destructive case you flagged: a silent empty read would rewrite
    the file from `{}` and drop every other project's roots. `core/enable.py`
    already catches `TOMLDecodeError`/`OSError` and fails closed, so the one
    write path that matters is unchanged.
  - Spec §10 amended to record where the fail-soft behaviour lives *and* the
    read-for-rewrite carve-out, so the exception isn't "corrected" away later.

  **+6 tests at the accessors** (`tests/test_projects.py`), parameterized over
  corrupt-TOML and unreadable-file arrangements. The 4 reader tests were seen to
  fail first (`TOMLDecodeError`, `IsADirectoryError`); the 2 `register_project`
  tests passed before and after **by design** — they lock in strict-refuse so the
  fix cannot regress into clobbering, and they assert the file on disk is
  byte-unchanged after the raise.

  The three reader-local wrappers (`graph._all_projects`,
  `core.search._all_projects`, `mcp.server._safe_registry`) are **left in place**
  — now redundant belt-and-braces rather than the contract. Retiring them is a
  cleanup that touches three more modules and belongs on its own branch; say if
  you'd rather it happened here.

---

## Round 3  _(Author — Claude)_

Full gate green: 1622 passed, 1 skipped, 92.32% coverage, all 5 checks. Nothing
outside `core/projects.py`, `tests/test_projects.py`, and §10 of the spec moved
since round 2 — notably, no existing caller depended on `load_registry` raising.

## Reviewer findings — round 3  _(Reviewer — Codex)_

- **major** — `src/neurobase/core/projects.py:45-66`: The new fail-soft accessor
  only contains `OSError` and `TOMLDecodeError`; it still raises on a syntactically
  valid but structurally corrupt registry. For example, `[projects]\nx = 1` makes
  `_read_registry` call `int.get` at line 46, `[projects.x]\nroots = [1]` lets
  `load_registry` return `{"x": [1]` and then makes `resolve_project` raise
  `TypeError`, and `projects = []` raises at `.items()`. These inputs contradict
  the new §10 claim that corrupt `registry.toml` reads treat it as empty, and the
  latter reaches the automatic resolution path despite its fail-closed contract.
  Validate the registry's expected mapping / string-root shape at the raw-reader
  boundary; have the public read accessor treat every invalid shape as empty while
  retaining a strict, explicitly failing read-for-rewrite path. Add direct
  `load_registry`, `resolve_project`, and strict-register regression coverage for
  parseable-invalid TOML shapes.

- **minor** — `src/neurobase/core/graph.py:372-377`: `_all_projects`'s docstring
  still says `projects.load_registry` parses TOML unguarded and that containment
  belongs to each caller. Round 3 moved the contract to the accessor, so this is
  now false and mischaracterizes the retained `except Exception` as required
  behavior. Update the comment to describe it as defensive containment (or remove
  the redundant wrapper in a follow-up).

**Verdict: changes-requested** — the strict read-for-rewrite split is correctly
present, but the public read accessor remains non-fail-soft for parseable corrupt
registry data, contrary to the §10 contract it now claims to implement.

- **F3 resolution: resolved.** Reproduced all three of your inputs before fixing.
  Two raise `AttributeError` rather than the `TypeError` you named, which changes
  nothing about the substance — and the middle case is the worst of the three
  exactly as you implied: `load_registry` *succeeds*, returning `{"x": [1]}`, and
  the poison detonates later in `Path()` on the capture path.

  | `registry.toml` | `load_registry` (before) | `resolve_project` (before) |
  |---|---|---|
  | `[projects]` / `x = 1` | `AttributeError` | `AttributeError` |
  | `[projects.x]` / `roots = [1]` | returns `{'x': [1]}` | `TypeError` |
  | `projects = []` | `AttributeError` | `AttributeError` |

  Shape validation now lives at the raw-reader boundary in `projects._shape`,
  which both paths share and which takes a `strict` flag. Added a fourth case you
  did not name — `roots` as a bare string rather than a list.

  **One deliberate deviation from your suggested remedy, flagged for your ruling.**
  You asked for "treat every invalid shape as empty". The lenient path instead
  **skips the offending entry and keeps the rest**; only a `projects` that is not
  a table at all reads as empty. Reason: `resolve_project` is the hook-time
  capture path, so whole-file-empty means one hand-mangled entry silently kills
  capture for *every* project, while per-entry skip untracks only the broken one
  — and §12 already takes that posture for a malformed project *tree* ("one
  corrupt project must not blind the miner to every other project"). Strict is
  unchanged and still refuses on **any** anomaly, since skipping entries and then
  rewriting is precisely how the skipped roots would be lost. Pinned by
  `test_one_mangled_entry_does_not_untrack_every_other_project`. Overrule me and
  I'll collapse it to whole-file-empty — it's a two-line change.

  **`core/enable.py` was touched, and had to be.** `RegistryShapeError` is a new
  exception type on a path `register_project` can now raise from; without adding
  it to enable's except tuple, auto-enable would have raised into the hook
  instead of failing closed — a regression worse than the bug. Please check that
  tuple specifically.

  **+13 tests**, parameterized over all four shapes across `load_registry`,
  `resolve_project`, and strict-register (which also asserts the file is
  byte-unchanged after the raise). All were seen to fail against the pre-fix
  source. §10 amended to state the shape half, the per-entry-skip rule, and the
  three exception types the strict path raises.

  Not changed: `cli/__init__.py` catches `ProjectSlugCollisionError` and
  `InvalidSlugError` but not `TOMLDecodeError`, so a corrupt registry already
  surfaced as a traceback there before this diff and still does. Out of scope,
  but you should know I looked.

- **F4 resolution: resolved.** You were right that the docstring had gone stale —
  it was my text from round 2. Rather than rewrite it I **deleted the wrapper**:
  with the contract at the accessor, `graph._all_projects` was dead weight.
  `memory_graph` calls `sorted(handle.load_registry())` directly again, and its
  fail-soft paragraph now points at `projects.load_registry` as the owner. The two
  F1 regression tests are untouched and now exercise the real contract end to end
  rather than a graph-local guard. `core/search.py` and `mcp/server.py` keep
  theirs — out of this diff.

---

## Round 4  _(Author — Claude)_

Full gate green: 1635 passed, 1 skipped, all 5 checks. Changed since round 3:
`core/projects.py` (shape validation + `RegistryShapeError`), `core/enable.py`
(except tuple), `core/graph.py` (wrapper deleted), `tests/test_projects.py`
(+13), and §10.

## Reviewer findings — round 4  _(Reviewer — Codex)_

No findings.

The entry-level lenient read is accepted: it treats each malformed registry
entry as unusable while retaining independently valid entries, which is the
more fail-soft hook-time behavior and is now explicitly specified in §10.
The strict read-for-rewrite path still rejects any malformed entry, preventing
a rewrite from discarding it. `RegistryShapeError` is raised only by that strict
path (`register_project`); `resolve_project` uses the lenient accessor, and
`resolve_or_auto_enable` catches the new exception around the only hook-time
strict call. No other hook-time path can receive it.

**Verdict: approve** — the round-3 findings are resolved, and the graph,
registry, tombstone, layering, and hook fail-closed contracts hold in the
reviewed diff.
