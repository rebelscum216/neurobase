# Web UI — app shell execution plan (Phase 2 → G → S → T → N)

_2026-07-16 — working plan. This is a plan, not a contract. Supersedes the
"visual direction" and later-phase sketches in
[`2026-07-15-webui-phase1-plan.md`](2026-07-15-webui-phase1-plan.md) (whose
Phase 1 scope is built and unaffected); companion to
[`2026-07-16-provenance-plan.md`](2026-07-16-provenance-plan.md), which
supplies the graph's data. The visual target is the interactive vision mockup
(claude.ai artifact "Neurobase — App Vision", 2026-07-16): an app, not a web
page — fixed shell, left rail, canvas graph as the home surface, skill gallery,
internal-scrolling panes._

## What changes, what carries

Phase 1 shipped a document-shaped UI (top bar, centered 960px column, tables).
The re-skin is almost entirely `base.html`: all five child templates do only
`{% extends "base.html" %}` + content blocks and are shell-agnostic (verified),
so the shell swap preserves every route, the CSRF middleware, the install
choreography, and all Phase 1 tests. Everything below rides on the existing
law: loopback-only bind hard-pinned in `webui/app.py:serve` (no `--host`,
ever), reads are side-effect-free GETs, mutations are CSRF-gated POSTs,
`webui/` never imports `cli/` (the peer rule is one-directional in practice —
the `ui` command lazily imports webui by design), no npm, no build step, new
Python deps (none anticipated) would be direct base deps.

**First functional JavaScript in the codebase** (the tracked root `index.js`
is a 4-line npm name-reservation placeholder, not code — decide whether it
should be deleted when real JS lands). Decision: inline `<script>` blocks in
templates, no `StaticFiles` mount, no external assets — consistent with the
inline-`<style>` posture and the zero-build ethos. The graph renderer (~200
lines of vanilla canvas JS, ported from the vision mockup) is the largest
piece; everything else is small progressive enhancement (view switching is
server-side routing, not a SPA — each rail destination is a real page).

## Design tokens (from the vision mockup)

Dark-first, both themes; carried as CSS custom properties in `base.html`:
cool near-black ground (`#0A0F14` / light `#F4F6F7`), panel layers, synapse
teal accent (`#2FCFC0` dark / `#0C8C86` light), **violet `#8B7BE8`/`#6E5BD6`
as the Codex agent color** (Claude = teal), semantic green/amber/rose reserved
for status chips, system sans for UI, `ui-monospace` for slugs/counts/refs.
Fixed-height app shell (`100dvh` grid: 240px rail + main), panes scroll
internally, zero document scroll. Motion is gated globally: every animation —
the rail health pulse, skill-card hover lift, inspector slide, graph ambient
drift — sits behind `prefers-reduced-motion`, exactly as the mockup does.

## Phase 2 — App shell + Sessions & Memory readers

_Goal: the app frame exists and the two read surfaces the old plan promised
land inside it._

**2a — shell re-skin (`base.html` only).** Left rail: brand, nav
(Graph · Sessions · Memory · Skills · Suggestions · Status) with live counts,
"recent sessions" list, store-health footer. Slim toolbar: view title +
search field. Nav entries whose routes don't exist yet render disabled with a
phase tag (honest, like the mockup). Counts come from a shared per-request
context helper (`_shell_context(request)` in `routes.py`): registry projects
(`projects.load_registry`), raw counts (`store.list_raw(...,
unconsumed_only=False)`), proposal counts by status
(`proposals.load_all_proposals`). The helper renders on **every** page, so its
registry read is wrapped fail-soft the way `core/search.py` wraps it — a
corrupt `registry.toml` degrades the rail, never 500s the whole UI. Same
per-request re-read posture as Phase 1; no caching, no watcher.

**2b — Sessions.** `GET /sessions` (store-wide, `?project=` filter driven by a
rail project switcher) — day-grouped list from `list_raw` across registry
projects: agent badge, title, prompts kept, consumed/unconsumed, project. The
title is best-effort display sugar: first line of the first `- ` bullet under
`## Prompts`, truncated — prompts are multi-line with embedded headings
(verified against real dev-store captures), so the parse must not assume
section discipline; the filename is the identity and the fallback. `GET /sessions/{project}/{file}` — reading
pane rendering the raw body (already redacted at capture; filename validated
against the raw-basename discipline before path join, mirroring
`corpus._is_safe_raw_basename`). Read-only; no ungating change (loopback +
GET-only remains the read-side posture, as Phase 1 established).

**2c — Memory.** `GET /memory` (per-project) — curated facts (slug, body,
pinned marker from the `user-directed` sentinel, updated_at), the synthesized
node rendered with a tiny internal renderer (headings/lists/`[[wikilinks]]`
only — no markdown dependency), tombstones in their grace window (reusing the
`store.list_tombstoned` helper that lands with provenance Slice A), and the
curator trend from `read_fact_count_trend`. Fact/node bodies additionally pass
through display-time redaction, mirroring §12.8's posture for drafts — cheap
symmetry; the curator's own output is the one write path spec permits to land
unredacted. `GET /search?q=` wired to `core/search.py`
(grep+TF over curated + nodes, fail-soft) — this is what the toolbar search
submits to.

**2d — Suggestions restyle.** The mockup's two-pane Suggestions surface (queue
of cards on the left, detail with score strip / evidence / draft / actions on
the right) is a real layout change the token re-skin alone cannot produce —
`suggestions_list.html` / `suggestion_detail.html` get restructured inside the
new shell. Template-only: routes, handlers, install choreography, and the CSRF
flow are untouched, and Phase 1's mutating-route tests are the unmodified
oracle.

_Done when: both agents' sessions are browsable in the shell, a fact's pinned
state and lineage are visible, search returns ranked hits, and the suggestions
surface is the mockup's queue+detail, passing Phase 1's tests unchanged._

## Phase G — Graph home surface

_Depends on provenance plan Slice A (`core/graph.py`); Slice B enriches it but
is not a blocker. Sequenced after Phase 2 so the shell exists to host it._

- `GET /graph` — the app's `/` destination once it ships (redirect target
  changes from `/suggestions`). Server-rendered page; graph JSON embedded in a
  `<script type="application/json">` block **via Jinja's `tojson` filter**,
  which escapes `<`/`>`/`&` as `\uXXXX` precisely so JSON can sit inside a
  script element — both naive paths verifiably break (autoescape corrupts the
  JSON with entities; `|safe` lets a `</script` inside captured prompt text
  terminate the block). No fetch endpoint for v1, no CSRF implications, page
  reload = refresh.
- Composition in the route layer: `core.graph.memory_graph(root)` for
  session/fact nodes + session→fact and fact→fact edges; then the
  `_evidence_rows` pattern (`proposals.load_all_proposals` +
  `corpus.EvidenceRef.from_frontmatter` + `resolve_evidence`) adds proposal/
  skill nodes and all three evidence-kind edges — `curated` → fact→proposal,
  `raw` → **session→proposal** (both accepted skills in the dev store carry
  only raw evidence; without this they float disconnected), `proposal` →
  proposal→proposal. The route layer also owns display derivation for nodes
  (session title per the 2b rule, prompts-kept count, fact body snippet) —
  `core/graph.py` stays identity-only.
- Renderer: the mockup's canvas module (~186 lines, verified self-contained —
  CSS-variable colors, ResizeObserver, reduced-motion static path) plus one
  piece the mockup does not have: a **deterministic layout**. The mockup
  hand-places nodes; the real renderer seeds per-project clusters with
  slug-hash jitter and runs a short bounded relaxation loop — no randomness,
  stable across reloads (~40 extra lines). Hover traces neighbors; click opens
  the inspector (session → facts it fed; fact → sessions it came from, what it
  distilled into, pinned state; skill → provenance and a jump to the gallery).
  Sessions colored by agent (teal/violet), facts as rings, skills as diamonds;
  the legend chips and the "hover to trace · click to inspect" hint pill carry
  over from the mockup. Unattributed facts render as orphans — **no
  run-granular edges** (provenance plan A6). Tombstoned facts optional/dimmed.
- Cross-navigation lands here: Sessions rows (2b) gain their "fed N facts"
  count and a deep-link to their graph node, matching the mockup's
  sessions-row → graph jump; the rail recents already link here.
- Scale posture, measured: the dev store (~90 raws, 27 facts, 4 proposals)
  embeds as ~44 KiB of JSON and a full per-request re-read costs ~19 ms — the
  Phase 1 no-cache posture holds with an order of magnitude of headroom.
- Respect `prefers-reduced-motion`; degrade to a static render.

_Done when: clicking a conversation in the rail lights up the facts it fed and
the skills those facts became, against the real dev store._

## Phase S — Skill library gallery

- `GET /skills` — gallery of cards. Enumeration: proposals with
  `status: accepted` (frontmatter `installed_path` + `target`), joined
  fail-soft with ledger `accepted` events (install time, `installed_hash`,
  ADR-0011) via `proposals.ledger_history`.
- Card contents: glyph tile, slug, draft description (redacted via
  `proposals.redact_body` at display time, per §12.8), provenance line from
  the **stored scores** (`recurrence`/`breadth` are already persisted in
  proposal frontmatter — no re-walk of evidence), type/target chips derived
  from the real enums (`type ∈ {skill, rule}`; `target ∈ {user-skill,
  project-skill, AGENTS.md, CLAUDE.md}` — "rule" is a type, not a target),
  install state.
- **Honest states, verified against the real dev store:** `installed_path` can
  dangle — **both** accepted proposals there point at SKILL.md files that no
  longer exist on disk. Cards check existence fail-soft and render `installed`
  / `missing on disk` / `modified` (hash mismatch, the survival-metric
  discipline) distinctly. Proposed/draft cards link into the Suggestions flow.
- Filters (all / installed / proposed / drafts, matching the mockup's chips)
  are server-side query params; hover lift/glow is CSS only.

_Done when: the gallery reflects on-disk truth — against today's dev store
that means both accepted skills render `missing on disk` — and every card
round-trips to graph and suggestions._

## Phase T — Status & control

**Prerequisite refactor (the D-1 pattern):** `doctor`'s checks live in
`cli/diagnostics.py` (`collect_checks` → frozen `Check(name, status, detail,
remedy)` records) and webui may not import cli. The module is cleanly liftable
— it imports adapters, brain, and core, zero cli internals (verified). But its
destination is **not** mid-tier: it imports two adapter *packages*, so it sits
in a new stratum above the mid tier and below the presentation edges. Import
direction stays downward-legal; `docs/architecture.md`'s layer diagram gains a
row, and the layer change is named in the same commit's ADR/spec note rather
than discovered later. `cli/diagnostics.py` becomes a thin re-export or the
CLI import path updates; `doctor`'s printed output and exit codes preserved
byte-for-byte, `tests/test_cli_doctor.py` (exists by that name) as the
unmodified oracle.

- `GET /status` — registry projects with counts, per-agent hook health from
  `collect_checks` (✓/!/✗ + remedy), brain backend resolution, store
  schema/root, last curator pass per project from `.curator-log.jsonl`
  (fail-soft tail read; fold summary once Slice B lands).
- **Run buttons, no streaming machinery:** `POST /status/curate/{project}`
  and `POST /status/recommend` (CSRF-gated like every mutation) spawn the
  detached-subprocess pattern the recall hooks already use
  (`recall_common.py:112-122`: `Popen(start_new_session=True,
  stdio→DEVNULL)`), then 303-redirect with a flash ("curator started — refresh
  to see the pass"). The curator log section is the result surface. Streamed
  output is explicitly out of scope (it would drag in SSE/state machinery this
  stack doesn't need yet). Three mechanics the naive version gets wrong,
  verified against the code:
  - **Curate is per-project and cwd-resolved** — `curate` has no `--project`
    flag; a bare spawn inherits the *server's* cwd and exits 1 invisibly
    ("Not an enabled project", stdio at DEVNULL). The button is per-project
    and passes `--cwd <first registered root from registry.toml>`, exactly
    what the hook spawner does (`recall_common.py:116`). If promoting the
    hidden testing `--cwd` into a UI contract rankles, the alternative is a
    small public `--project` option on `curate` — decide in review.
    `recommend run` is store-wide and needs only `--root`.
  - **Binary resolution:** the hook pattern execs `sys.argv[0]`, which is only
    correct because hooks are registered with an absolute shim path. Under
    uvicorn, `sys.argv[0]` is whatever launched the server (breaks under
    `python -m neurobase`). Resolve like `shim_path()` does
    (`adapters/claude/install.py:39-53`): `shutil.which("neurobase")` falling
    back to a resolved `sys.argv[0]`.
  - **Single-flight:** a per-project in-process guard so a double-click
    doesn't race two LLM passes. Cross-process overlap with the hooks'
    opportunistic `curate --if-stale` spawn remains possible and **pre-exists
    this UI** (there is no store locking anywhere; concurrent passes race on
    read-modify-write upserts and a fixed `.tmp` name) — record it as a
    known-gaps candidate rather than solving it here.
- Spawning the CLI binary from webui is process composition, not a code
  import — the peer rule (no lateral *imports*) holds, and spec §3/D8 already
  blesses the identical detached-CLI-spawn pattern for hooks; note it in the
  webui spec section.

_Done when: doctor's verdict is readable in the shell and both LLM passes are
one gated click, with their results appearing on reload._

## Phase N — Native shell (unchanged from the original plan)

Swift `WKWebView` wrapping `neurobase ui` — spawns the server, waits for
health, loads it; menu-bar item; thin shell, no logic. Optional, Mac-only,
last.

## Law & process

- **Spec promotion — at step 0, not Phase T:** Phase 1 already ships mutating
  routes that install real artifacts from the browser, with the loopback-only
  and CSRF rules living only in a notes plan + tests. Every prior behavioral
  surface got its spec section + ADR when it was built (recommender §12 /
  ADR-0007, MCP §13 / ADR-0008). So the webui section (a §14: bind, CSRF,
  GET/POST posture, install choreography parity) + its ADR land **when the
  Phase 1 branch merges**, and later phases extend it — rather than stacking
  four phases on an unspec'd write surface.
- **Sequencing:** (0) land `feat/webui-phase1-suggestions` through the Codex
  review relay first — everything stacks on it; (1) provenance Slice B, then
  Slice A on main; (2) Phase 2a+2b+2c as one branch (shell + readers); (3)
  Phase G; (4) Phase S; (5) Phase T (with its refactor as a separate commit);
  each branch tightly scoped, full `scripts/ci.py` gate + 3-OS matrix, review
  relay per AGENTS.md.
- **Testing:** follow the split-by-area pattern (`test_webui_app.py` /
  `test_webui_suggestions.py`) — new `test_webui_sessions.py`,
  `test_webui_memory.py`, `test_webui_graph.py`, `test_webui_skills.py`,
  `test_webui_status.py`. tmp_path stores only; never `target='user'`
  installs; mutating-route tests use the established CSRF+origin idiom and
  assert on-disk state through core/recommender functions. Template/JS gets a
  smoke assertion (page renders, embedded JSON parses), not a browser harness.
- **Out of scope, deliberately:** filesystem watching / live refresh (browser
  reload is the refresh; revisit only if it hurts), htmx or any JS framework,
  a `/graph.json` public-ish API surface (the JSON stays embedded), streamed
  command output, exposing the server beyond loopback (deploy-safety gate
  applies the moment that is ever proposed).
