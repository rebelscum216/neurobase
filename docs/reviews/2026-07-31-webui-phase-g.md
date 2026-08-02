---
slug: webui-phase-g
status: changes-requested
author: claude
reviewer: codex
branch: feat/webui-phase-g-graph
diff: git diff origin/main...HEAD
created: 2026-07-31
rounds: 4
last_reviewed: 0dc83450e0648a893064ae42239be829fb90b678
---

> **Status note (2026-07-31).** The Brief below was written to *open* the relay
> and describes the branch at `7267633`. Four rounds have since run; the branch
> is four fix commits further on. The Brief is left as written — it is the
> record of what was handed over — and the outcome is recorded under
> [Reviewer findings](#reviewer-findings--reviewer--codex) at the end.

# Review: Phase G — the Web UI graph home surface

## Brief  _(Author — Claude)_

**Intent.** Make `GET /graph` the app's home surface: `/` redirects here and the
rail entry goes live. It draws the memory constellation — sessions, curated
facts, skills, and the edges between them — from provenance Slice A's data, and
lets you click a node to see what it connects to and *how*. This is the
app-shell plan's Phase G and the **first real functional JavaScript in the
codebase**.

**Scope.** Branch `feat/webui-phase-g-graph`, `git diff origin/main...HEAD`
(single commit `7267633`, PR #11, CI green ×4). Key files:

- `src/neurobase/webui/graph_view.py` — **new, ~470 lines.** The composition
  layer: `graph_payload(handle, root, *, include_orphan_sessions=False)`, the
  display derivation (session title per the plan's 2b rule, prompts-kept count,
  fact snippet), and the proposal/evidence half of the graph.
- `src/neurobase/webui/templates/graph.html` — **new, ~890 lines.** The page:
  CSS, markup, the canvas renderer ported from the 2026-07-22 vision mockup, a
  deterministic layout the mockup does not have, the inspector, and the
  keyboard/deep-link wiring.
- `src/neurobase/webui/routes.py` — `graph_routes()` + `_graph_home`; `/` now
  redirects to `/graph` instead of `/suggestions`.
- `src/neurobase/webui/shell.py` — `_NAV` graph entry flipped to `enabled=True`;
  removes a `_is_active` branch for `path == "/"` that never executed.
- `src/neurobase/webui/templates/base.html` — a `head_extra` block and a
  `.page.full` full-bleed variant.
- `tests/test_webui_graph.py` — **new, 25 tests.** `tests/test_webui_app.py` —
  the root-redirect test retargeted to `/graph`.

**Focus areas.** Roughly in order of how much I want a second opinion:

1. **The two containment guards in `graph_view`.** `_raw_body` re-applies the
   two-part guard from `routes._capture_path` (resolved parent must be *exactly*
   the project's `raw/`, **and** the file must be inside the store) because
   `name` arrives from a curated fact's hand-editable `provenance:` list and is
   carried verbatim through `core/graph.py` — a name failing the raw-filename
   grammar still becomes a skeleton session's id. `_fact_snippets` applies the
   `handle.contains` filter every peer reader uses (Codex P2-SAFETY-SECURITY-003).
   Both were **found by review, not by the test suite or a browser pass** — the
   first as a working path traversal. Please attack them independently rather
   than reading them as already-blessed: is the parent check sufficient given
   `.resolve()` semantics, and is there any other reader in this new module that
   touches the filesystem without one of these guards?
2. **The connectivity filter is a product decision, not just a perf tweak.** The
   plan's scale posture ("~90 raws … ~44 KiB") predates attribution; the real
   store holds ~2,100 captures of which only ~78 sessions carry any edge, so
   drawing them all is ~900 KiB rendered as a cloud of disconnected dots. The
   default view therefore hides unattributed **sessions** (never facts), states
   the hidden count on the page, and offers `?orphans=1`. Is a home surface that
   silently omits 90%+ of captures defensible with only that disclosure? The
   count is deliberately in **captures**, not sessions, because sessions collapse
   many raws into one node and the pill sits beside a rail badge counting raws.
3. **`memory_graph(handle, include_tombstoned=True)`.** The curator writes
   `supersedes:` and tombstones the old fact in the *same* fold, so with the
   default a fresh supersession yields no edge and only pruned ancient history
   shows one (1 → 15 edges on the dev store). But this also means tombstoned
   facts now render. Is admitting them the right call, or should supersession
   edges be sourced without also drawing their tombstoned endpoints?
4. **The JS, as the first real JS here.** Specifically: the layout is
   deterministic by construction (FNV-1a over node ids, no `Math.random`, no
   time input) — verify a reload genuinely cannot reshuffle it. The relaxation
   uses a uniform spatial grid whose cell equals the interaction radius; the
   O(n) claim in the comment holds only at bounded density, which I have **not**
   proven for a pathological single-project store. The render loop cancels on
   `visibilitychange`/`pagehide` and parks when the camera settles under reduced
   motion, but under normal motion it runs forever by design — is that
   acceptable for a page meant to sit open?
5. **Fail-soft breadth in `_graph_home`.** It catches bare `Exception` and
   renders a "could not be read" state, mirroring `shell_context`'s reasoning
   that the app chrome must never 500 the page it decorates. That is broad. It
   deliberately does **not** wrap `open_store`, so `UnsupportedSchemaError`
   still reaches `unsupported_schema_handler` (D11). Is the breadth justified
   here, and is "degraded" visibly distinct enough from "empty"?

**Known risks / tradeoffs.**

- **A local `/code-review` pass already ran against this branch and found 15
  defects, all fixed in this commit** (plus 5 below-cap items and 2 dead-code
  removals; 10 of the 25 tests are regressions pinning them). So the obvious
  layer is likely swept — the value here is in what a fresh reading finds that a
  same-session reviewer would not. Two of that pass's claims did **not**
  reproduce under check: `provenance: 5` does not raise `TypeError` (the fact is
  skipped upstream), and the disclosure-pill unit error was ~1.5% on the real
  store rather than the ~6× claimed. Both fixes were kept on their own merits.
  Do not treat that pass's conclusions as verified.
- **Performance is honest but not good.** `/graph` is ~630 ms on the dev store.
  ~310 ms is `core.memory_graph` (Slice A's own cost over ~2,100 captures) and
  ~300 ms is the pre-existing `shell._counts`, which YAML-parses every raw in
  every project on *every* page render to compute a `len()`. My layer adds ~56 ms.
  The handler is a plain `def` so it runs in Starlette's threadpool rather than
  blocking the event loop. Fixing `_counts` needs a counting accessor on
  `StoreHandle` (ADR-0015 chokepoint discipline forbids globbing the raw root
  from the UI layer) — out of scope here, deliberately.
- **Ported from the 2026-07-22 mockup, not the 07-16 one the plan cites.** The
  shipped `base.html` already follows the 07-22 palette, so the plan's §Design
  tokens section is stale. Correcting that doc is a follow-up, not in this diff.
- **The 2b session title is best-effort display sugar.** It is parsed at column
  zero only, so an indented heading pasted inside a prompt cannot forge the
  title or the prompts-kept count; the filename remains the identity and the
  fallback.
- Interaction paths (hover-trace, card traversal, `← back`, Escape, arrow-key
  walk) were verified by headless screenshots and reasoning, **not** by a human
  driving a browser.

**How to verify.**

```
git fetch origin && git checkout feat/webui-phase-g-graph
uv sync --all-extras && make ci          # expect 1660 passed, 91.82% coverage
uv run neurobase ui                      # then visit /
```

Worth exercising by hand: click a hub fact (many neighbours) and check the
relationship line and its `journal` / `frontmatter` chip against that fact's
actual `provenance:` and the curator journal; toggle `?orphans=1`; reload a
`?node=…` URL and use browser back; try `?node=constructor` (a prototype-
pollution id that must not blank the canvas); tab into the canvas and arrow-key.

**Out of scope.**

- `shell._counts` performance, and the `graph_view._raw_body` re-read of files
  `memory_graph` already read (needs a Slice A API change).
- The Codex transcript renderer (ADR-0013 S-cf3) and the curator `fallback`
  counter that conflates "undistillable by design" with "backend failed" —
  found while curating today, unrelated to this diff.
- Correcting the app-shell plan's §Design tokens section.
- `lendergraph` having curated facts but no registry entry.
- Phases S / T / N of the app-shell plan.

---

## Reviewer findings  _(Reviewer — Codex)_

**Verdict after 4 rounds: ⚠️ CHANGES REQUESTED.** The loop was stopped by the
repo owner at round 4 rather than run to APPROVED — the last round's fixes
(`213828f`) are committed but **were never re-reviewed**. Severity trend:
CHANGES REQUESTED → BLOCKED → CHANGES REQUESTED → CHANGES REQUESTED. No P0, and
no P1 since round 2.

Round SHAs, each an immutable artifact the round was pinned to:

| Round | Reviewed | Verdict |
|---|---|---|
| 1 | `7267633` | CHANGES REQUESTED — 6 × P2 |
| 2 | `f9fb9c5` | **BLOCKED** — 1 × P1 + 3 × P2 |
| 3 | `43e2826` | CHANGES REQUESTED — P1 closed, 3 × P2 |
| 4 | `0dc8345` | CHANGES REQUESTED — 001–008 all closed, 1 × P2 new + 2 × P3 |
| — | `213828f` | **not reviewed** — fixes 009, 010, 011, 012 |

**Closed and verified by the reviewer (001–008).** Containment of external
document identity across five channels — raw, curated, tombstone, the curator
journal, and proposal documents — each found separately and each closed at the
enumeration boundary; a `RuntimeError` fail-soft so one symlink loop cannot blank
the home page; tombstoned facts no longer emitting a guaranteed-404 link;
proposal nodes no longer fabricating kind or install state; the relaxation loop
rewritten from quadratic-under-dense-buckets to hard-bounded (2,100 nodes
346→145 ms, 4,000 nodes 1,256→264 ms, overlapping pairs 572→241 — bounded *and*
better separated); a D11 schema refusal re-raised explicitly at both catch sites
rather than degraded into a 200; and the JS half given a real executable
behaviour suite after a source assertion was shown green while the O(n) claim it
named was false.

**Open at the point the loop was stopped** — all four addressed in `213828f`,
none re-reviewed:

- `P2-SAFETY-SECURITY-010` — the project registry was an uncontained **sixth**
  identity channel: a symlinked `registry.toml` selects which project namespaces
  the sweep walks. Contained at `StoreHandle.load_registry()`, the shared
  accessor, because twelve call sites reach it and `_artifact_state` reopens the
  store.
- `P2-ROLLOUT-CONFIG-009` — `node` became a required gate tool without the
  contributor docs saying so. Swept repo-wide; also corrected a wrong version
  floor, a documented command that does not work, and a "no `package.json`"
  claim that was false.
- `P3-MAINTAINABILITY-011` — the repulsion comment claimed an equivalence the
  Gauss-Seidel update does not have.
- `P3-UX-API-CONTRACT-012` — the legend and canvas aria label named only
  "skill", though rule proposals draw the same glyph.

**Gate at this tip:** `make ci` green — ruff, format, mypy, store-chokepoint,
**1,683 passed / 1 skipped**, and 20 JS tests; py3.11 and py3.13 1,683 each; the
JS suite on Node 22 and Node 24.

The full round-by-round record, including every finding's text, the reviewer's
independent probes, and the mutation verification for each fix, is kept outside
this repo in the relay's tracking card.
