# ADR-0019: Folder-scoped auto-enable — consent once at a directory, not per repo

- **Status:** Proposed
- **Date:** 2026-07-23
- **Resolves:** the per-repo `enable` friction; relocates the opt-in gate (spec §3/§4/§5/§10)
- **Supersedes:** none

## Context

Turning Neurobase on for a repo is **two** steps today, and they are often
conflated:

1. **Install the hooks.** `neurobase init` writes the SessionStart/SessionEnd
   entries. This *already* has a global mode — `neurobase init --user` writes them
   once into `~/.claude/settings.json` (and `~/.codex/`), so they fire for every
   session on the machine. Per-repo hook install is only the *default*, not the
   only option.
2. **`neurobase enable` the repo.** This registers the repo in `registry.toml` and
   creates its memory tree. It is the **opt-in gate**: both scribes and recall
   no-op in any repo without a tree.

So the "I have to remember to add Neurobase to each project" friction is really
step 2 — the per-repo `enable`. That gate is **deliberate and spec-stated**:

- Spec §4 (Claude scribe): *"Opt-in: write only if the resolved project's memory
  tree exists"* ([`spec-appendix.md:350`](../neurobase-spec-appendix.md)); §5
  mirrors it for Codex.
- Both scribes and `build_context` enforce it in code:
  [`scribe.py`](../../src/neurobase/adapters/claude/scribe.py) returns `None` on an
  untracked cwd or a tree-less project; recall
  ([`recall_common.py`](../../src/neurobase/adapters/recall_common.py)) injects
  nothing.
- "Consent-first, zero-surprise capture" is a stated product promise (README): a
  session is captured only into a repo the user explicitly opted in.

Because the gate is a spec contract and part of the product's identity, automating
it is a **change to a locked behavior, not a routine implementation choice** — the
kind the [ADR README](README.md) says must be recorded rather than "quietly edited
into code." Any acceptable fix must respect four existing constraints:

- **The consent-first promise must survive.** A silent "capture everything,
  everywhere" default would break it outright.
- **Neurobase never writes `config.toml`** (spec §10: *"hand-edited by the user"*).
  A mechanism that requires Neurobase to write config contradicts a shipped
  invariant.
- **Hooks stay fail-safe** — exit 0, never wedge a session (§4/§5), inside the
  ADR-0003 latency budget.
- **All store access routes through the `StoreHandle` chokepoint** (ADR-0015): no
  registry/tree mutation may skip the D11 schema guard.

The per-project memory-tree model, worktree-collapse resolution
([`projects.resolve_project`](../../src/neurobase/core/projects.py)), and capture
redaction must all be preserved unchanged.

## Decision

Add **folder-scoped auto-enable**: the user names a directory once, and any git
repo beneath it is registered — and given its tree — the first time a hook fires
there. This **relocates** consent from per-repo to per-folder; it does not remove
it. Empty configuration = today's exact per-repo behavior.

**D39 — The `[enable]` config section is the unit of consent.** Add to
`config.toml` (spec §10):

```toml
[enable]
auto_enable_roots = ["~/Projects"]        # folders whose repos auto-enable
denylist = ["~/Projects/client-work"]     # subtrees carved back out (wins over roots)
```

Both are hand-edited lists of **absolute or `~`-prefixed** paths (a relative entry
would resolve against the hook's launch cwd — non-deterministic scope — so it is
skipped; review F5); Neurobase never writes them (invariant preserved). **Naming a
directory in `auto_enable_roots` *is* the consent act** — deliberate and explicit.
`denylist` is a **live gate** (review F4): it wins over `auto_enable_roots` *and*
revokes an already-enabled repo — adding a repo to `denylist` stops its capture on
the next hook even though it is still in the registry. So consent is genuinely
revocable by editing one line, in both directions. An empty (or absent)
`auto_enable_roots` is the default and means *"per-repo `enable` only"* —
byte-for-byte today's behavior.

**D40 — Auto-enable is git-repo-scoped: one project per repo, denylist wins.** The
policy is a pure function,
[`projects.auto_enable_root_for(cwd, roots, denylist)`](../../src/neurobase/core/projects.py):

- `cwd` **must be inside a git repo**; the repo's *common* root becomes its own
  project (worktrees collapse to one project, exactly as `resolve_project` does).
- A non-git directory never qualifies — so the umbrella folder itself
  (`~/Projects`) is never captured as one giant catch-all project.
- A repo whose root sits under any `denylist` path never qualifies — the denylist
  **wins over** `auto_enable_roots`, so a sensitive subtree can be carved out of an
  otherwise-enabled folder.
- Configured paths are `~`-expanded and resolved before comparison; a non-existent
  configured path matches nothing rather than raising.

**D41 — One resolution seam, through the chokepoint, fail-closed.** Add
[`core/enable.resolve_or_auto_enable(root, cwd, *, auto_enable_roots, denylist)`](../../src/neurobase/core/enable.py)
— the single place all three hook surfaces (both scribes §4/§5, recall §3) route
project resolution through. It:

1. checks the **live `denylist`** first (F4) — a denylisted cwd returns `None`
   before anything else, so it neither auto-enables nor keeps capturing;
2. resolves the **registered** project (READ handle — the D11 guard runs, no
   write); returns it if found;
3. otherwise consults `auto_enable_root_for`; a non-qualifying cwd returns `None`
   (the caller no-ops exactly as before — and, being READ-only, still creates no
   `store.toml`);
4. for a qualifying cwd, opens a **WRITE handle** so the schema guard runs *before*
   the registry/tree mutate (closes the same mutate-before-guard class ADR-0015 D23
   closes for `init`), then returns the new slug.

**Fail-safe ordering (review F2).** The qualifying branch derives and validates the
slug *before* opening the WRITE handle (an un-sluggable name skips out leaving a
pristine store — no `store.toml`), then creates the **tree before** writing the
registry entry, all inside one guarded block whose `except` covers
`UnsupportedSchemaError`, slug collision, `InvalidSlugError`, **and `OSError`** →
`None`. Writing the registry only *after* the tree exists means a tree failure can
never leave a **registered-but-treeless** project — which would otherwise match
`resolve_project` forever and never be retried, silently killing that repo's
capture. So it is genuinely fail-closed, not merely wrapped by the hook's outer
`except`. This registration is the **only** registry write on a hook path, reached
at most once per repo.

**D42 — The trigger is session-start (recall), with the scribes as backstop; the
seam is opt-in per call site.** Recall runs first in a session, so it is where the
tree gets created: on the first session in a qualifying repo, recall auto-enables,
finds no nodes yet (curate hasn't run), and injects nothing — correct. Session-end
capture then lands, curate folds it, and by the next session recall injects
normally. Both scribes carry the same seam as a backstop for sessions with no start
hook. Crucially, auto-enable is **opt-in per call site** (review F1):
`build_context(..., auto_enable=False)` by default, and only the SessionStart hook
(`emit`) and the scribes pass `auto_enable=True`. The MCP `recall` prompt shares
`build_context` but keeps the read-only default, so a prompt *read* never mutates
the store or raises a registry/FS error out of that fail-soft surface (spec §13).

**Explicitly out of scope (deferred, not decided here):**

- **A config-writing `neurobase enable --scope <dir>` CLI.** Nicer than
  hand-editing, but it would make Neurobase *write* `config.toml`, contradicting
  the §10 invariant. That is its own decision about who owns `config.toml`; the
  config-only mechanism ships first, and the CLI — if wanted — gets a follow-up
  ADR.
- **Global, folder-less auto-enroll** ("capture every repo you ever open"). See
  Alternatives — rejected as a default.

## Consequences

- **Install-once UX.** `neurobase init --user` (once) + one `[enable]` stanza →
  every git repo under the named folder is captured with no further per-repo
  action, including repos created *after* the config is written.
- **Consent is relocated, not removed.** With `auto_enable_roots` empty, every
  scribe/recall path behaves byte-for-byte as before (regression-tested:
  `test_scribe_without_config_stays_opt_in`). The consent-first promise holds — the
  granularity of consent moves from a repo to a folder the user deliberately named.
- **Opt-out within an opted-in folder is the new mental model — call it out.** A
  brand-new repo cloned under an `auto_enable_root` is captured by default; a user
  who wants a carve-out uses `denylist`. This is the *intended* semantics of
  folder-level consent, but it is a genuine shift from "nothing is captured until I
  say so" to "everything under here is captured unless I say not to." Accepting
  this ADR is accepting that shift for repos under a named root.
- **A hook path now performs a registry write** (once per repo, through a WRITE
  `StoreHandle`, so the schema guard is not bypassed). This is a **new
  concurrent-writer exposure** the serial manual `enable` never had (review F7):
  `register_project` is an unlocked read-modify-write with tmp+replace, so two
  *different* repos' first sessions racing (e.g. two IDE windows) can have the
  second `tmp+replace` clobber the first's just-added entry. The file never tears
  (atomic replace), and the dropped repo self-heals on its next session — but one
  repo can be unregistered for one session. If lost first-registrations ever matter,
  guard the RMW with a lock/retry. No locking is introduced now.
- **Registered wins over auto-enable (review F6).** Because the seam resolves the
  registered project first, if an *ancestor* of a new repo is already registered
  (e.g. `~/Projects` itself, or a monorepo root), a brand-new child repo under an
  `auto_enable_root` folds into the ancestor's project via longest-prefix match
  rather than getting its own — a deliberate "registered wins" precedence, noted
  here so it is not a surprise.
- **Latency.** When `auto_enable_roots` is empty the policy returns immediately (no
  git call added). When non-empty, an *unregistered* repo pays a second
  `git rev-parse` (`resolve_project` then `auto_enable_root_for` each resolve the
  git root — review F8b; could be threaded through once if it ever matters), plus a
  registry write + `mkdir` on the first session only. A non-empty `denylist` adds
  one more git resolve on the live-gate check. All well within the ADR-0003 budget —
  it is the same work `enable` does, done once per repo.
- **Redaction and capture fidelity unchanged.** Auto-enabled repos capture through
  the identical scribe path (§4/§5 redaction, bounds, empty-skip).
- **Spec appendix updates (this ADR is the proposal; the spec is the law — fold in
  on implement):** §10 gains the `[enable]` keys and the folder-scoped resolution
  rule; §4/§5's opt-in line becomes *"write only if the resolved project's tree
  exists **or the repo qualifies for folder-scoped auto-enable (§10), which creates
  the tree**"*; §3/§7's consent narrative gains the folder-consent model.
- **Implemented + self-reviewed.** On `feat/folder-scoped-auto-enable` (full CI gate
  green; 27 tests in
  [`tests/test_auto_enable.py`](../../tests/test_auto_enable.py)). A self-review
  (Codex was unavailable) surfaced F1–F8, recorded in
  [`docs/reviews/2026-07-23-folder-scoped-auto-enable.md`](../reviews/2026-07-23-folder-scoped-auto-enable.md);
  F1/F2/F4/F5/F7/F8 are resolved here, F6 documented above. **An independent Codex
  review is still owed** before merge — the self-review is weaker by construction.

## Alternatives considered

- **Global, folder-less auto-enroll** (capture every repo you open a session in) —
  **rejected as a default.** It breaks the consent-first promise hardest: throwaway
  clones, other people's code, and sensitive repos are all captured with no
  boundary the user chose. Folder-scope gives a *bounded* consent unit the user
  named. A loud, explicit "capture everywhere" flag could be offered later, but it
  is not the model here.
- **Batch pre-registration — `neurobase enable --recursive <dir>`** — **rejected as
  the primary mechanism.** It is explicit and simple, but only registers repos that
  exist *at scan time*; repos created later need a re-scan, so it does not deliver
  "install once and forget." Useful as a complementary bulk command, not a
  substitute for the auto path.
- **Just document `neurobase init --user`** (make global hooks the recommended
  path) — **rejected as sufficient.** It removes the hook-install step but leaves
  the per-repo `enable` — the actual friction — untouched.
- **A config-writing `enable --scope` CLI as the mechanism** — **deferred, not
  rejected.** Better UX than hand-editing, but it makes Neurobase write
  `config.toml`, contradicting §10. Ships after the config-only mechanism, behind
  its own decision about config ownership.
- **Auto-enable only at capture-time (scribe), not session-start** — **rejected as
  the trigger.** It works, but the tree would not exist for the session's own
  recall or any mid-session tooling. Firing at session-start means the project is
  live for the whole session; the scribes keep the seam only as a backstop.
