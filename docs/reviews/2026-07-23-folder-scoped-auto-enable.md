---
slug: folder-scoped-auto-enable
status: changes-requested   # self-review (Codex unavailable); independent pass still owed
author: claude
reviewer: codex
branch: feat/folder-scoped-auto-enable
diff: git diff main...HEAD
created: 2026-07-23
---

# Review: Folder-scoped auto-enable — consent once at a directory, not per repo

## Brief  _(Author — Claude)_

**Intent.** Remove the "remember to `neurobase enable` every repo" friction by
**relocating** the capture opt-in from per-repo to per-folder. A new `[enable]`
config section names `auto_enable_roots`; when a hook fires in a git repo that is
not a registered project but whose root sits under an `auto_enable_root` (and not
under `denylist`), Neurobase registers it as its own project and creates its memory
tree, then captures/injects through the ordinary opt-in path. Empty config =
today's exact per-repo behavior.

This is a **prototype pending ADR-0019** (`docs/adr/0019-folder-scoped-auto-enable.md`,
status **Proposed**). The ADR is the governing design; **the spec appendix has NOT
yet been folded** (that happens on Accept). So this change *intentionally* alters
the §4/§5 opt-in wording — please review it as a proposed, ADR-backed change, not
as an unsanctioned spec violation. The product go/no-go on folder-consent is the
Router's call at the relay gate; design-soundness concerns are welcome as findings.

**Scope.** Branch `feat/folder-scoped-auto-enable`, `git diff main...HEAD` (two
commits: `a84c97a` prototype, `c4ef441` ADR). Key files:
- `src/neurobase/core/config.py` — new `EnableConfig { auto_enable_roots, denylist }`, loaded in `load_config` (still never *written* by Neurobase).
- `src/neurobase/core/projects.py` — `auto_enable_root_for(cwd, roots, denylist)`: pure, git-repo-scoped path policy (+ `_is_within` helper).
- `src/neurobase/core/enable.py` _(new)_ — `resolve_or_auto_enable(...)`: the single resolution seam; resolves the registered project first, auto-registers only a genuinely untracked *qualifying* cwd via a WRITE `StoreHandle`, fails closed (→ `None`) on a too-new store / slug collision / un-sluggable name.
- `src/neurobase/adapters/claude/scribe.py`, `src/neurobase/adapters/codex/scribe.py` — route resolution through the seam (§4/§5 capture); reuse the one `load_config()`.
- `src/neurobase/adapters/recall_common.py` — `build_context` auto-enables at session-start (§3), so the first session in a qualifying repo creates the tree.
- `tests/test_auto_enable.py` _(new, 15 tests)_ — policy, seam, and end-to-end scribe integration.
- `tests/test_recall_common.py` — the `SimpleNamespace` fake config gains an `enable` section (build_context now reads it).
- `docs/adr/0019-*.md`, `docs/adr/README.md` — the ADR + index row.

**Focus areas.** Where I most want your eyes:
1. **Fail-safe preserved (AGENTS.md principle #3 / spec §4/§5).** `resolve_or_auto_enable` must never raise into a hook. Confirm every path — bad config paths, `git` absent, too-new store, slug collision, un-sluggable repo — returns `None` rather than propagating, and the scribes/recall still exit 0.
2. **Chokepoint discipline (ADR-0015).** All store access must go through `open_store()`/`StoreHandle`; `enable.py` must not construct store paths or read `registry.toml`/`store.toml`/`memory/` directly. Confirm the READ-then-WRITE ordering keeps the D11 guard *before* any registry/tree mutation, and that a non-qualifying resolution stays READ-only (creates no `store.toml`).
3. **Policy correctness (`auto_enable_root_for`).** Git-repo-scoped (non-git dir never qualifies, incl. the umbrella folder itself); worktrees collapse to one project; `denylist` wins over `auto_enable_roots`; prefix matching uses resolved paths so a sibling like `~/Projects2` doesn't match `~/Projects`. Edge cases you can think of that the tests miss.
4. **Consent-first (AGENTS.md principle #6 / spec §7).** Is relocating consent to the folder an acceptable reading of "consent-first"? Is the empty-config regression truly byte-for-byte today's behavior?
5. **Tests enforce the contracts** the change touches (principle #1) — especially the fail-closed and regression cases.

**Known risks / tradeoffs.**
- A hook path now performs a **registry write** (once per repo, via a WRITE handle). Concurrency is unlocked read-modify-write with tmp+replace — the same as `neurobase enable` today; worst case is a benign idempotent re-register. No new locking introduced.
- **Mental-model shift:** a new repo cloned under an `auto_enable_root` is captured by default (opt-out via `denylist`), vs. today's "nothing until I opt in." This is the intended semantics of folder-consent and is called out in the ADR Consequences.
- **Spec appendix not yet updated** — deliberate; ADR-0019 is Proposed. If you'd rather I fold §10/§4/§5 now, flag it.
- **No config-writing `enable --scope` CLI** — deferred (it would break §10's "never writes config.toml"); ADR records it as a follow-up.

**How to verify.**
- Full gate: `uv run python scripts/ci.py` (ruff + format + mypy + pytest w/ coverage floor) — green on this branch.
- Targeted: `uv run pytest tests/test_auto_enable.py tests/test_recall_common.py tests/test_claude_scribe.py tests/test_codex_scribe.py -q`.
- Manual: set `[enable] auto_enable_roots = ["<tmp-workspace>"]` in a temp `config.toml`, run a scribe against a fresh git repo under it, confirm it registers + captures; add the repo's parent to `denylist` and confirm it no-ops.

**Out of scope.**
- The `enable --scope` CLI and global folder-less auto-enroll (both deferred/rejected in the ADR).
- Re-litigating the folder-consent product direction itself — that's the Router's gate. (Design concerns are still fair findings.)
- The in-flight ADR-0015 chokepoint CI-guard work (lives on a different branch; not in this diff).

---

## Reviewer findings  _(self-review — Codex unavailable)_

> **Provenance.** Codex is out of credits (≈1 week), so this is **not** the
> independent Codex pass. It is an author-run self-review conducted via two
> fresh-eyes reviewer subagents (safety/invariants lens + correctness/tests lens)
> that saw the diff and repo rules but not the author's rationale, plus the
> author's own verification of each claim against the code. A self-review is
> weaker than the relay's independent pass by construction — **independent Codex
> review remains an open follow-up** before this merges. Findings below were each
> verified against the actual code (A1 confirmed at `server.py:314`).

### F1 — MCP `recall` prompt now triggers an unauthorized store write and can raise (§13)
- **severity:** major
- **location:** `src/neurobase/adapters/recall_common.py:86` reached from `src/neurobase/mcp/server.py:314`
- **issue:** `build_context` is shared with the MCP `recall` prompt handler, which passes the server's launch cwd and is **not** wrapped (the `contextlib.suppress` at `server.py:306` only covers `_register_node_resources`). Adding auto-enable into `build_context` means a prompt *read* can now (a) register a project + create a tree from an MCP surface ADR-0019 D42 never authorized, and (b) raise `OSError` (mkdir/registry write) straight out of the handler — spec §13 requires MCP to never error. Gated behind `mcp.expose_resources=True` (off by default), which limits but does not remove it.
- **suggested direction:** make auto-enable opt-in *per call site* — e.g. `build_context(..., auto_enable=False)` default; scribes/recall pass `auto_enable=True`, MCP keeps the read-only default. (Belt-and-braces: wrap the `recall()` handler like the resource scan.)
- **resolution:** _(Author fills)_

### F2 — `resolve_or_auto_enable` is not fail-safe as documented; partial registration poisons a repo
- **severity:** major
- **location:** `src/neurobase/core/enable.py:55-70`
- **issue:** the docstring/ADR D41 claim it "yields `None` rather than raising," but `writer.ensure_tree(slug)` is **outside** the `try`, and the `except` only catches `UnsupportedSchemaError` / `ProjectSlugCollisionError` / `InvalidSlugError`. `OSError` (mkdir / `_write_registry`) and a corrupt-registry `tomllib.TOMLDecodeError` escape. The three hook call sites' outer `except Exception` keeps the *exit-0* invariant intact, but (i) the function's own contract is false and the MCP caller (F1) is unwrapped, and (ii) worse: if `register_project` succeeds but `ensure_tree` then fails, the repo is left **registered-but-treeless** — `resolve_project` matches it forever, so `auto_enable_root_for` is never re-consulted, the tree is never retried, and every future session silently no-ops. A one-time FS hiccup permanently kills that repo's capture. Also leaves a `store.toml` behind on a pristine store when a qualifying repo hits an invalid-slug/collision skip (the WRITE handle is opened before `register_project` can fail), contradicting the "never creates store.toml as a side effect" comment.
- **suggested direction:** bring `ensure_tree` inside the `try`; broaden `except` to `OSError` (and `tomllib.TOMLDecodeError`) → return `None`; don't leave a registration written when the tree can't be created (create the tree before/with the registry entry, or roll back). Consider deriving the slug / checking collision before opening the WRITE handle so a skipped enable writes no `store.toml`.
- **resolution:** _(Author fills)_

### F3 — Test gaps: the ADR's *primary* trigger (recall) and the Codex scribe are untested, plus claimed edges
- **severity:** major
- **location:** `tests/test_auto_enable.py`, `tests/test_recall_common.py`
- **issue:** every integration test drives the Claude scribe. D42 names **recall** as where the tree gets created first — no test calls `build_context`/`emit` with a non-empty `auto_enable_roots` to assert register + tree + `None` (no nodes yet). The **Codex scribe** got the identical seam edit but is a separate copy with zero auto-enable coverage. Also untested despite being claimed: worktree collapse (D40), the sibling-prefix false-positive (`~/Projects` vs `~/Projects2`), and fail-closed *at the scribe surface* (only the seam is tested). AGENTS.md principle #1: every MUST gets a test.
- **suggested direction:** add a recall e2e test mirroring `test_scribe_auto_enables_repo_under_configured_root`; a Codex-scribe analogue; a `git worktree` collapse test; a sibling-prefix negative; and a scribe-level too-new-store fail-closed test.
- **resolution:** _(Author fills)_

### F4 — Consent is not retroactively revocable; ADR overpromises "revocable by editing one line"
- **severity:** major (design/semantics)
- **location:** `src/neurobase/core/enable.py:47-49`; claim in `docs/adr/0019-folder-scoped-auto-enable.md` D39/D40
- **issue:** `resolve_or_auto_enable` returns an already-registered slug **before** the `denylist`/root policy runs, so adding a repo to `denylist` (or removing its `auto_enable_root`) does nothing once the repo has been enabled — capture continues and the tree keeps accumulating. The ADR advertises the denylist as the carve-out mechanism and consent as "revocable by editing one line"; neither holds after first enable.
- **suggested direction:** decide the intended semantics: either (a) make `denylist` a **live gate** — re-check it against the resolved repo root even for a registered project, so denylisting stops capture — or (b) correct the ADR to state that denylist only gates *first* enable and revocation requires deregistration. (a) matches the promise but is a real behavior change worth its own note.
- **resolution:** _(Author fills)_

### F5 — Relative config paths resolve against the hook process cwd (non-deterministic scope)
- **severity:** minor
- **location:** `src/neurobase/core/projects.py:153,156`; comment at `config.py` EnableConfig
- **issue:** the config comment permits "relative segments," but `Path(p).expanduser().resolve()` resolves a relative entry against wherever the hook binary was spawned, so the auto-enabled set shifts with launch cwd — a relative `denylist` entry can silently fail to protect its target.
- **suggested direction:** require absolute or `~` paths (skip/warn on non-absolute after expansion), or resolve against a fixed base; update the comment.
- **resolution:** _(Author fills)_

### F6 — Resolve-first folds a new child repo into a manually-registered ancestor project
- **severity:** minor (unstated interaction)
- **location:** `src/neurobase/core/enable.py:47-52` + `projects.resolve_project` longest-prefix match
- **issue:** if an *ancestor* of a new repo is already registered (e.g. `~/Projects` itself, or a monorepo root), resolve-first returns the ancestor's slug for a brand-new child repo under an `auto_enable_root`, folding it into the ancestor rather than giving it its own project — contra D40's "one project per repo." Defensible as "registered wins," but unstated.
- **suggested direction:** document the precedence; add a test pinning intended behavior.
- **resolution:** _(Author fills)_

### F7 — Concurrency: two different repos' first sessions can drop a registration; ADR reasoning is single-repo
- **severity:** minor (doc)
- **location:** `docs/adr/0019-folder-scoped-auto-enable.md` Consequences; `projects._write_registry` (unlocked RMW) via `enable.py:61`
- **issue:** the ADR calls the worst case a "benign idempotent re-register," which assumes one repo. With folder-scope, two *different* repos' first sessions racing (two IDE windows) each load the same base registry and the second `tmp+replace` clobbers the first's new entry — one repo is unregistered for that session (self-heals next session; no torn file). New concurrent-writer exposure the serial manual `enable` never had.
- **suggested direction:** correct the ADR's concurrency note; add a lock/retry around the registry RMW only if lost first-registrations matter.
- **resolution:** _(Author fills)_

### F8 — Nits
- **severity:** nit
- **location:** `src/neurobase/core/projects.py:127`; `src/neurobase/core/enable.py:47/51`; `src/neurobase/adapters/recall_common.py:94-96`
- **issue:** (a) `_is_within`'s `path == ancestor` disjunct is redundant — `Path.is_relative_to` already returns `True` for equal paths. (b) `git_common_root(cwd)` runs twice per unregistered cwd (`resolve_project` then `auto_enable_root_for`); the ADR understates the cost as "prefix checks" when it's a second `git` subprocess. (c) the `recall_common.py` comment "READ never writes, so recall creates no store.toml here" is now misleading — the call two lines above can create it.
- **suggested direction:** drop the redundant disjunct (or keep as intent doc); thread the repo root through once; fix the comment.
- **resolution:** _(Author fills)_

**Verdict:** changes-requested — no blockers, but F1/F2 (safety) and F3 (coverage of the designated trigger) should land before merge, and F4 is a genuine consent-semantics decision the ADR currently gets wrong. Code is otherwise logically sound: policy path-matching, redaction, slug derivation, and empty-config behavioral equivalence all verified clean. **This verdict is a self-review standing in for the independent Codex pass, which is still owed.**
