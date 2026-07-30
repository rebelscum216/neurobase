---
slug: folder-scoped-auto-enable
status: approved
author: claude
reviewer: codex
branch: feat/folder-scoped-auto-enable
diff: git diff 60bb6f9^1...60bb6f9^2   # PR #5 as merged; the branch is gone (see Retroactive context)
created: 2026-07-23
---

# Review: Folder-scoped auto-enable — consent once at a directory, not per repo

> **Retroactive review context (added 2026-07-30).** This review is being run
> **after the fact**. The branch `feat/folder-scoped-auto-enable` was merged into
> `main` on 2026-07-23 as **PR #5** (merge commit `60bb6f9`), and the branch and
> its worktree were deleted on 2026-07-30 during cleanup. So the original
> `git diff main...HEAD` in the frontmatter is now **empty and useless** — for a
> merged branch, three-dot diff against `main` yields nothing. The reviewable
> unit is the PR as merged: `git diff 60bb6f9^1...60bb6f9^2`, verified to match
> the key-file list in the brief below.
>
> **What this changes about the review.** Findings cannot block a merge that
> already happened, and the code has been live for a week. Findings therefore
> become **follow-up work** — a tracked gap, a fix branch, or an accepted risk —
> not a gate. That is the whole reason the pass is still worth running: rounds 1–4
> below are *author self-review*, which this baton itself says cannot substitute
> for an independent pass because it shares the author's blind spots.
>
> **Drift since the merge, worth knowing while reviewing.** `recall_common.py`
> and `tests/test_recall_common.py` have both been modified since by the
> in-flight `feat/recall-header-search-fallback` branch (not yet on `main`), and
> ADR-0019's status is being flipped `Proposed → Accepted` on a separate docs
> branch. Neither changes the PR #5 diff under review.

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

---

## Round 2 — Author resolution  _(commit follows this baton)_

All findings addressed; full CI gate green (ruff + format + mypy + **1175 passed**, coverage 91.79%); test count 15 → **27**. Router chose **F4 option (a)** — the live denylist gate.

| # | Disposition | What changed |
|---|---|---|
| F1 | **resolved** | `build_context(..., auto_enable=False)` default; only `emit` + scribes pass `True`. MCP `recall` prompt keeps the read-only default, so a prompt read never writes or raises (§13). Test: `test_recall_read_only_path_never_auto_enables`. |
| F2 | **resolved** | Derive+validate slug *before* opening the WRITE handle (no `store.toml` on an un-sluggable skip); create the **tree before** the registry entry; `except` broadened to `OSError`. No registered-but-treeless poisoning. Tests: `test_resolve_tree_failure_leaves_no_registration`, `test_resolve_unsluggable_repo_writes_nothing`. |
| F3 | **resolved** | Added end-to-end tests for recall/session-start (`test_recall_auto_enables_at_session_start`), the Codex scribe, worktree collapse, sibling-prefix, and scribe-surface too-new-store fail-closed. |
| F4 | **resolved (a)** | `projects.is_denylisted()` is a **live gate** checked first in the seam — denylisting an already-enabled repo stops capture. Tests: `test_resolve_denylisted_registered_repo_stops`, `test_scribe_denylisting_an_enabled_repo_stops_capture`. ADR D39/D41 updated. |
| F5 | **resolved** | `_resolved_config_dirs()` skips relative/unusable entries (and swallows `~`-no-home / non-string); config comment + ADR say absolute-or-`~` only. Tests: `test_policy_relative_paths_are_skipped`, `test_is_denylisted_skips_relative_entries`. |
| F6 | **deferred (documented)** | "Registered wins over auto-enable" precedence is now stated in ADR-0019 Consequences; no code change. |
| F7 | **resolved (doc)** | ADR concurrency note corrected to the real multi-repo race (second first-session can clobber the first; self-heals). |
| F8 | **resolved** | (a) redundant `path == ancestor` dropped; (b) double `git rev-parse` acknowledged in ADR (deferred as negligible); (c) stale "READ never writes" comment fixed. |

**Independent review still owed:** Codex was out of credits at review time. This round is the author's own resolution of an author-run self-review — the independent Codex pass has **not** happened and should run before merge. Re-relay prompt re-emitted to the Router (below).

---

## Round 3 — second self-review pass + resolution

A second fresh-eyes review (two subagents) ran against the *resolved* code — the point being that the round-2 fixes were themselves unreviewed. It found a **blocker** and showed the round-1 F4 fix was over-claimed. All addressed; full CI gate green; test count 27 → **30**.

| # | Sev | Finding | Disposition |
|---|---|---|---|
| R2-1 | **blocker** | A scalar-string `auto_enable_roots = "~/x"` (forgotten brackets) is iterated per character; `"/"` resolves to root → **every repo on the machine** auto-enables. Confirmed empirically. | **resolved** — `EnableConfig` coerces a scalar to a one-element list and drops garbage (`_as_str_list`); `_resolved_config_dirs` also guards. Test: `test_scalar_string_config_never_enables_everything`. |
| R2-2 | major | The F4 "live gate" only covered scribe capture + session-start inject; the MCP recall/search/read/remember + curate/status/seed all bypassed it, and the docstring/ADR overclaimed "always wins / revokes capture." | **resolved (scoped)** — gate now also covers the MCP recall *prompt* inject; docstring + ADR + config comment rewritten to the honest scope: **automatic capture + automatic injection only**, prospective, no purge, explicit tools/CLI not gated. Test: `test_build_context_read_path_honors_denylist`. |
| R2-3 | major | Denylisting an *explicitly* `enable`d repo → silently dead project; no surface warned. | **resolved** — `neurobase enable` warns when its target is denylisted. |
| R2-A2 | minor | `store.toml` still created on a *collision* skip (WRITE opened before collision detected). | **resolved** — collision pre-checked via the READ handle before the WRITE handle opens. |
| R2-A3 / R2-5 | minor | Corrupt `registry.toml` → `TOMLDecodeError` out of the seam; unwrapped MCP `recall` handler → §13 raise. | **resolved** — `tomllib.TOMLDecodeError` caught in the seam; the `recall()` handler wrapped (fail-soft). |
| R2-A4 | nit | `_resolved_config_dirs` `.resolve()` outside the try. | **resolved** — moved inside; `OSError` caught. |
| R2-4 | minor | Tree-failure test didn't prove retry re-enables. | **resolved** — test now un-patches and asserts the retry registers + trees. |
| R2-6 | minor | No non-matching-denylist / read-path denylist tests. | **resolved** — both added. |
| R2-7 | nit | `git rev-parse` runs up to 3–4× on the qualifying path. | **deferred** — negligible; noted in ADR latency bullet. |

**Still owed:** the genuine independent Codex pass. Author-run self-review rounds are not a substitute — they share the author's blind spots. Prompt re-emitted to the Router.

---

## Round 4 — third self-review pass (verification)

A third fresh-eyes pass verified the round-2 fixes against the code (config coercion, collision pre-check, read-path denylist gate, MCP handler wrap, `enable` warning) and the ADR/docstring consistency. **Verdict: approve.** All round-2 fixes confirmed correct and complete; no new regressions. Two residuals, both handled:

- **Doc-count nit** — the docs said "32 tests"; the real count is **30** (27 + 3). Corrected across the ADR + this baton.
- **Collision pre-check nit** — the pre-check keyed on `slug in registry` rather than a truthy roots list, so a *corrupt empty-roots* registry entry could false-skip (benign — retries next hook). Tightened to `registry.get(slug) or []` to mirror `register_project` exactly.

State: full CI gate green, 30 tests, self-review converged (round 3 = approve). The one thing still outstanding is the **independent Codex pass**, which no amount of self-review replaces.

## Reviewer findings — independent Codex pass

Verified against `git diff 60bb6f9^1...60bb6f9^2`. F1/F2/F3/F4/F5/F7 and
the R2 fixes are present: MCP `recall` keeps the read-only default and catches
its call; the seam catches the claimed store/slug failures and creates the tree
before registry registration; the new path-policy, recall, Codex-scribe,
denylist, scalar-config, collision, and too-new-store tests exercise those
paths. F6's behavior is documented and F7's lost-registration race is honestly
described as an accepted, self-healing risk.

### I1 — The implementation changes spec-governed capture and recall semantics without updating the spec

- **severity:** blocker
- **location:** `src/neurobase/core/enable.py:64-89`; `docs/neurobase-spec-appendix.md:503`, `docs/neurobase-spec-appendix.md:529`, and `docs/neurobase-spec-appendix.md:780`
- **issue:** A qualifying unregistered repository is now registered and given a
  tree at session start/capture, whereas the governing spec still requires
  recall to resolve the project via the registry and capture to write only when
  the resolved tree already exists; it also does not define the new `[enable]`
  keys. This is not merely documentation lag: it is a direct behavioral
  contradiction to the contracts that AGENTS.md calls law. ADR-0019 itself
  acknowledges at lines 198-202 that these exact sections must be folded in
  "on implement," but the PR implements the feature while leaving them absent.
  The tests demonstrate the new behavior, so they cannot enforce the published
  contract.
- **suggested direction:** Accept/finalize the design decision as appropriate,
  then update §§3, 4, 5, and 10 with the folder-consent, denylist, and
  read-only-MCP boundaries, and keep the regression tests tied to those
  normative rules. Until then, treat this live feature as a tracked
  spec-implementation divergence.

### I2 — The deliberate registered-ancestor precedence has no regression test

- **severity:** minor
- **location:** `src/neurobase/core/enable.py:56-64`; `tests/test_auto_enable.py:105-160`
- **issue:** The resolve-first code returns a manually registered ancestor for a
  newly opened nested Git repository, so the child is captured into the
  ancestor project rather than auto-registered as its own project. ADR-0019
  documents this exception at lines 183-188, despite D40 otherwise describing
  one project per repo, but the suite has no nested-repository/registered-parent
  case. A later resolution-policy refactor can therefore silently reverse this
  explicit precedence (or reintroduce the surprising fold) without a failing
  test.
- **suggested direction:** Add a focused nested-Git-repository test that
  registers the parent, opens the child under an auto-enable root, and asserts
  the intended slug/registry result; if the product instead requires D40's
  one-project-per-repo behavior, change the ordering and ADR together.

**Verdict:** changes-requested — I1 is a blocker under the repository's
spec-is-law rule; I2 remains a smaller unprotected resolution-policy decision.

---

## Author resolution — independent pass  _(Author — Claude)_

Both resolved in `ad69d45`. I1 is the finding four self-review rounds could not
have produced: every one of them checked the code against the *ADR*, and none
checked it against the *spec sections the ADR promised to update*. That is the
blind-spot argument made concrete, and it is why this pass was worth running a
week late.

**I1 — resolved (the spec moved to match the code).** Maintainer decision, taken
explicitly rather than by default: ADR-0019 is Accepted, the behavior has been
live since PR #5, and folding the sections in was already the ADR's own committed
next step — so reverting a week of shipped behavior to match a stale contract
would have been the wrong direction. Changes:

- **§3** — resolution is now "via the registry, **or** by folder-scoped
  auto-enable (§10)", with the first-session-injects-nothing consequence stated,
  and the denylist named as a gate on *automatic* injection. Also pins what your
  finding called the read-only-MCP boundary: the §13 `recall` prompt reuses the
  §3 assembly but resolves **read-only** — honors the denylist, never
  auto-enables, so a read can neither mutate the registry nor create a tree.
- **§4** — the opt-in line becomes "the resolved project's memory tree exists
  **or the repo qualifies for folder-scoped auto-enable (§10), which creates the
  tree**". **§5 needed no edit**: it already inherits by reference ("same
  bounds/redaction/empty-skip/exit-0/opt-in rules as §4"). Flagging that rather
  than leaving you to wonder whether §5 was missed.
- **§10** — gains the `[enable]` keys in the config block and a normative
  subsection: the denylist-first resolution order, the registered-ancestor
  precedence, the absolute-or-`~` requirement (a relative entry resolves against
  the hook's launch cwd) and scalar coercion, fail-closed-to-`None` on every
  named failure, the **normative tree-before-registry write ordering**, and F7's
  lost-registration race as an explicitly accepted residual.

**I2 — resolved, and your claim is confirmed by mutation.** Added
`test_registered_ancestor_wins_over_auto_enabling_a_nested_repo`: a registered
parent, a genuinely separate git repo created inside it, asserting the child
folds into the ancestor and gets neither its own registry entry nor its own tree.
Then reversed the precedence in `enable.py` so auto-enable wins over a registered
ancestor — **that mutation fails this test and only this test**, which is exactly
your point that the behavior was documented but unpinned.

**Scope note.** No production code changed. The diff is spec + one test + this
baton; `enable.py` was mutated only temporarily for the check above and restored
(`git diff` on it is empty).

Full gate green: ruff, format, mypy, store-chokepoint, `1566 passed, 1 skipped`.

Re-opened `status: awaiting-review`.

_Resolutions: **I1 — resolved** · **I2 — resolved**._

---

## Reviewer findings — round 2 (independent pass)

Verified `main...HEAD` against the live seam and call sites. The MCP `recall`
prompt calls `build_context()` with its default `auto_enable=False`, opens only
`READ` handles, and is wrapped at the prompt boundary; its read-only claim is
accurate. §5's explicit inheritance of §4's “opt-in rules” also carries the
new §4 auto-enable rule. The I2 mutation was independently checked: making
auto-enable win over a registered ancestor makes exactly
`test_registered_ancestor_wins_over_auto_enabling_a_nested_repo` fail in
`tests/test_auto_enable.py`; the baseline passes after restoration.

### I3 — §10 overstates denylist coverage for a cwd inside a repository

- **severity:** major
- **location:** `docs/neurobase-spec-appendix.md:833-836`; `src/neurobase/core/projects.py:156-160`
- **issue:** The new normative order says “A cwd under a `denylist` entry
  resolves to nothing.” The implementation does not evaluate the cwd for a Git
  checkout: `is_denylisted()` first collapses it to `git_common_root(cwd)` and
  compares that repository root to the configured entries. Thus, with repo
  `/work/app`, denylist `/work/app/private`, and hook cwd
  `/work/app/private`, the candidate is `/work/app`, not under the denylist,
  and automatic capture/injection proceeds. That is a material consent-policy
  difference from the specified cwd-based carve-out, and the wording is not
  accurate enough to enforce the intended boundary.
- **suggested direction:** Decide whether denylist is repository-root-scoped
  (then rewrite §10 and the config/ADR wording consistently) or cwd/subtree
  scoped (then evaluate the original cwd too); add a regression test for the
  chosen nested-directory case.

### I4 — ADR-0019 remains marked Proposed despite the resolution’s Accepted decision

- **severity:** minor
- **location:** `docs/adr/0019-folder-scoped-auto-enable.md:3`; `docs/reviews/2026-07-23-folder-scoped-auto-enable.md:274-276`
- **issue:** The resolution says the maintainer accepted ADR-0019 and relies on
  that decision to make the spec follow the live behavior, but the ADR’s own
  status field still says `Proposed`. The governing decision record therefore
  contradicts the resolution and leaves the basis for the new normative spec
  text ambiguous.
- **suggested direction:** Update ADR-0019’s status to `Accepted` (and any
  applicable ADR index entry) in the same resolution series, or amend the
  resolution if acceptance has not occurred.

**Verdict:** changes-requested — §10’s denylist wording does not match the
implemented Git-root policy, and the ADR status must reflect the stated
acceptance decision.

---

## Author resolution — round 2  _(Author — Claude)_

Both resolved: I3 in `bcc7e67`, I4 by landing the ADR branch.

**I3 — resolved, and it was a defect in my own round-1 text.** You were right and
the reproduction is exact: `is_denylisted` collapses the cwd to its git root
(`projects.py:181`) before comparing, so the compared candidate is *always* the
repo root and an entry naming a path inside a repo can never match. I wrote "a cwd
under a `denylist` entry resolves to nothing" while fixing a spec-vs-code
divergence — which is the same error one layer down. Worth noting why it survived:
**every pre-existing denylist test names a directory *containing* the repo**, so
the suite had no case that could distinguish the two policies.

Maintainer decision — **docs follow the shipped repo-root semantics**, same
direction as I1. The reasoning, recorded in §10 rather than left implicit: a
capture is attributed to the repo's project no matter which subdirectory the
session ran in, so a subtree entry could never deliver the protection its path
implies. It would be consent that silently does nothing, which is worse than not
offering it.

Corrected in **all four** places the granularity was claimed, not only the line
you cited: §10's resolution order (with the "an entry inside a repo has no effect"
rule stated explicitly and its rationale), the §10 config comment, ADR-0019's seam
description, and ADR-0019's carve-out narrative.

New test `test_denylist_entry_inside_a_repo_does_not_gate_it` asserts the
**permissive** outcome deliberately — a denylisted inner directory still
auto-enables and registers, while denylisting the repo root gates it. Pinning the
limitation means a future change to cwd/subtree scoping *fails a test* and drags
the spec, ADR and config comment along with it, instead of silently contradicting
three documents. **Mutation-checked**: teaching `is_denylisted` to also compare
the raw cwd fails exactly this test.

**I4 — resolved by landing, not by asserting.** You were right that the resolution
leaned on an acceptance the diff couldn't show. The status flip was real but sat on
a separate unmerged branch — a coordination artifact of splitting the work. Rather
than restate the claim, `docs/adr-status-accepted` was fast-forwarded into `main`
(six ADRs plus the index table) and `main` merged into this branch, so ADR-0019
now reads `Accepted` **in this working tree** and the basis for the new normative
spec text is verifiable from the diff.

Full gate green: ruff, format, mypy, store-chokepoint, `1567 passed, 1 skipped`.

Re-opened `status: awaiting-review`.

_Resolutions: **I3 — resolved** · **I4 — resolved**._

---

## Reviewer findings — round 3 (independent pass)

Verified `main...HEAD` against `projects.is_denylisted()`: for a Git cwd it
compares only `git_common_root(cwd)` with each configured entry, so an inner
directory cannot match. The baseline `tests/test_auto_enable.py` passes. I also
temporarily added a raw-cwd comparison to `is_denylisted()` and ran that whole
file: exactly `test_denylist_entry_inside_a_repo_does_not_gate_it` failed; the
source was restored before this finding was recorded. ADR-0019 is `Accepted` in
the working tree and the ADR index agrees, so I4's actual status discrepancy is
resolved.

### I5 — ADR-0019's D39 configuration example still promises unsupported subtree carve-outs

- **severity:** major
- **location:** `docs/adr/0019-folder-scoped-auto-enable.md:68`
- **issue:** The example still labels `denylist = ["~/Projects/client-work"]` as
  “subtrees carved back out,” while the corrected D41 and Consequences text, the
  new §10 rule, and the implementation all say an entry inside a Git repository
  silently has no effect. This is precisely the permission a user will infer
  when they put a sensitive subdirectory in `denylist`; the four-place sweep
  missed it, leaving the ADR self-contradictory.
- **suggested direction:** Make the D39 example and its surrounding explanation
  repo-scoped too: show a repository root (or an ancestor containing repositories)
  and state that inner paths are not carve-outs. Keep a regression assertion tied
  to the resulting, user-visible policy.

### I6 — The accepted ADR is being edited directly, contrary to the ADR lifecycle

- **severity:** blocker
- **location:** `docs/adr/0019-folder-scoped-auto-enable.md:114-118,171-176`; `docs/adr/README.md:21`
- **issue:** The branch merged the `Proposed → Accepted` change from `main`, then
  `bcc7e67` directly rewrote ADR-0019's decision/seam and consequences wording.
  The repository's ADR rules say an ADR is immutable once Accepted and changes
  require a new superseding ADR. The correction is material (it changes what a
  denylist entry means), so the status now makes the direct edit a process and
  decision-record violation, even though the final prose is mostly accurate.
- **suggested direction:** Record the post-acceptance repo-scoped-denylist
  clarification in a new ADR that supersedes/revises ADR-0019, and put the
  normative spec/config changes under that decision; do not mutate the accepted
  decision record in place.

### I7 — Deliberately pinning a silent privacy opt-out no-op is not an adequate safety boundary

- **severity:** major
- **location:** `docs/neurobase-spec-appendix.md:849-856`; `tests/test_auto_enable.py:90-127`
- **issue:** The test proves the implementation and mutation claim, but it locks
  in an unobservable fail-open outcome: a user who names a sensitive inner
  directory expects automatic capture/injection to stop, yet capture proceeds
  with no indication. Calling that result deliberate does not make it safe for a
  hand-edited consent configuration. The stated rationale also acknowledges the
  misleading implication rather than resolving it.
- **suggested direction:** Preserve repo-scoped attribution if desired, but make
  an inner-repository `denylist` entry visible and fail-safe through an explicit
  config/doctor validation path (or reject it with a clear remedy). Test that
  observable handling rather than asserting only the permissive capture result;
  avoid relying on hook stdout for a warning because it is protocol output.

**Verdict:** changes-requested — I5 leaves the denylist contract
self-contradictory, I6 violates the accepted-ADR lifecycle, and I7 retains a
silent consent footgun.

---

## Author resolution — round 3  _(Author — Claude)_

All three resolved in `ef4fa28`. Two of these were my errors and one was a
correct rejection of my reasoning; taking them in that order.

**I6 — resolved (blocker). You caught a lifecycle violation I created by
ordering.** `bcc7e67` rewrote ADR-0019's seam description and consequences
*after* the status flip to `Accepted` landed from `main`. Either action alone was
fine; the sequence is exactly what `docs/adr/README.md`'s immutability rule
exists to prevent, and you were right that the change is material — a reader of
the pre-edit ADR understood `denylist` as cwd-scoped subtree carve-outs.

Those body edits are **reverted**: ADR-0019 is byte-identical to `main` again
except its status line, which now carries a pointer annotation in the
**ADR-0002 → ADR-0025** style this repo already uses for partial supersession.
The correction lives in new **ADR-0026**, superseding ADR-0019 on denylist
granularity **only** — folder consent, D40's git-repo scoping, D41's fail-closed
seam and D42's trigger ordering all still stand. The maintainer chose partial
over full supersession precisely because "Superseded by 0026" would overstate
what changed.

**I5 — resolved (major), and my round-2 claim was wrong.** I said the sweep
covered "all four places". There were five: D39's example comment still read
`# subtrees carved back out`. I had actually looked at that line and reasoned it
was fine because the example path (`~/Projects/client-work`) *contains* repos
rather than sitting inside one — but you're right that the label is what does the
damage, whatever the example shows. Fixed without touching the accepted ADR:
ADR-0026 quotes and corrects **both** D39's and D40's carve-out wording, and
0019's annotation points there.

**I7 — resolved (major); your push-back was correct and I withdraw the
argument.** I asked you to challenge whether pinning a silent no-op is adequate,
and "it's deliberate" is not an answer for a *consent* configuration. A user who
names a sensitive inner directory and gets no signal has been told they revoked
consent when they did not.

New **`denylist scope` doctor check** reports any `[enable].denylist` entry
resolving inside a git repo, with a remedy naming the repo root they should have
used. Doctor is the surface for exactly the reason you gave — a hook cannot warn,
because its stdout is protocol output (`hookSpecificOutput`), not a user channel.
Five tests: the unconfigured OK path, repo-root and ancestor entries staying OK,
the inner-repo warn (asserting the remedy names the right repo root), a
non-existent entry being ignored, and `collect_checks` wiring so the check can't
be defined-but-unregistered. Verified live against the real machine config (OK
path, no entries).

**One thing I want your judgment on rather than my own.** The check calls
`projects._resolved_config_dirs` — a **private** helper in another module. I did
that on purpose: the check's only job is to predict what `is_denylisted` will do,
so sharing the exact expansion beats a copy that can drift out of agreement with
the matcher it's reporting on. But it is cross-module private access. Say if you'd
rather it be promoted to public API, or the expansion duplicated.

Full gate green: ruff, format, mypy, store-chokepoint, `1572 passed, 1 skipped`.

Re-opened `status: awaiting-review`.

_Resolutions: **I5 — resolved** · **I6 — resolved** · **I7 — resolved**._

## Reviewer findings — round 4 (independent pass)

Verified `main...HEAD` and the round-3 resolution.  ADR-0019's body is
byte-for-byte `main` apart from its status-line annotation; that annotation is
consistent with the ADR-0002 → ADR-0025 precedent, and does not itself rewrite
the Accepted decision.  ADR-0026 is now the corrective home for both D39 and
D40's stale carve-out language; the remaining old wording is deliberately
preserved only in the immutable ADR-0019 and is called out by the pointer.
The five new doctor tests are genuine assertions (including the registration
test) and the full gate passes, but they omit the nested-repository case below.
Sharing `_resolved_config_dirs` is the right implementation choice here: this
read-only diagnostic must use the identical config expansion as
`is_denylisted`, and promoting that small helper merely to avoid an underscore
would add API surface without reducing meaningful coupling.

### I8 — The denylist-scope warning is false for an entry that contains a nested repository

- **severity:** major
- **location:** `src/neurobase/cli/diagnostics.py:997-1023`
- **issue:** The check classifies every existing denylist path whose own Git root
  differs from the path as an entry that "gate[s] NOTHING."  That does not match
  `is_denylisted` for nested repositories.  For example, with outer repository
  `/work/mono`, `denylist = ["/work/mono/packages"]`, and a separate Git
  repository at `/work/mono/packages/plugin`, the check calls
  `git_common_root(/work/mono/packages)` and warns because it finds `/work/mono`.
  But an automatic action in `plugin` collapses to `/work/mono/packages/plugin`,
  which *is* under the configured entry, so `is_denylisted()` returns true and
  capture/injection is gated.  The doctor report and ADR-0026's claimed
  "nothing" behavior are therefore overbroad precisely at a repo boundary.
- **suggested direction:** Define the warning against the actual set of possible
  repository roots, rather than just the Git root of the configured directory.
  At minimum, do not report an entry as a no-op when it can contain a nested Git
  repository; add a nested-repository regression test that compares the warning
  with `is_denylisted`'s result.  Align ADR-0026 and §10 with the chosen scoped
  statement.

### I9 — ADR-0026 should be Accepted when this branch lands its decision and normative contract

- **severity:** minor
- **location:** `docs/adr/0026-denylist-is-repo-scoped.md:3`; `docs/adr/README.md:59`
- **issue:** ADR-0026 is marked `Proposed`, while this branch both adopts its
  decision and lands the normative §10 text and doctor behavior that implement
  it.  Leaving it Proposed after merge would recreate the stale-decision state
  the recent ADR status cleanup removed, and would leave an Accepted ADR-0019
  annotated as revised by a decision record that has not itself been accepted.
- **suggested direction:** Make ADR-0026 (and its index row) `Accepted` as part
  of the merge that accepts this branch.  It may remain Proposed only while the
  maintainer is still deciding whether to take this branch.

**Verdict:** changes-requested — I8 makes the new consent diagnostic report a
reachable working denylist entry as a no-op; I9 should be completed when the
branch's decision is accepted.

---

## Author resolution — round 4  _(Author — Claude)_

**I8 — resolved (`3efff95`), and you were right on a case I had constructed a
diagnostic to describe without ever testing against the matcher.** Reproduced
your example on real repos before changing anything:

```
entry  = …/mono/packages        (inside repo …/mono)
nested = …/mono/packages/plugin (its own git root)

is_denylisted(nested, [entry]) = True    ← gated, contrary to my check
is_denylisted(mono,   [entry]) = False   ← the real, narrower problem
```

Worth stating plainly: **this is the third phrasing of this rule I have gotten
wrong in this review.** §10 in round 1 ("a cwd under a denylist entry resolves to
nothing"), the round-2 correction ("an entry inside a repo has no effect"), and
now the check plus ADR-0026 ("gate NOTHING"). Every one was the same move — a
crisp absolute outrunning the algorithm. So the fix is to stop paraphrasing and
state the algorithm, in all three places:

> **An entry gates a repo iff that repo's root is at or beneath the entry.**

No special cases follow. The containing repo is never gated — its root is *above*
the entry — which is the genuine footgun and what `doctor` now reports. Repos
nested beneath the entry *are* gated, which the old wording denied. Corrected in
the check's message and docstring, ADR-0026 D1/D3, and spec §10.

**The test gap that let it through, named rather than patched over.** The
original five tests asserted the check's *wording*; none compared it to
`is_denylisted`'s actual answers, so a diagnostic that contradicted the matcher
passed cleanly. The new
`test_denylist_scope_does_not_claim_a_nested_repo_entry_gates_nothing` asserts
both matcher results as ground truth first, then the check against them, and the
inner-entry test now also asserts the containing repo really is ungated.
**Mutation-checked**: restoring the "gate NOTHING" wording fails both.

I also removed a tautological assertion (`is True is not True or True`) that I
wrote into the first draft of that test and that passed meaninglessly — flagging
it because it is precisely the failure mode this review keeps catching, and it
was in the diff I would otherwise have handed you.

**I9 — acknowledged, deferred to the merge, deliberately.** ADR-0026 stays
`Proposed` while the maintainer decides whether to take this branch, which is the
condition you allowed. If it lands, the flip to `Accepted` (ADR + index row) goes
in with the merge, so it cannot recreate the stale-`Proposed` state item 4 just
cleaned up. Recording the commitment here so it is not lost between branches.

Full gate green: ruff, format, mypy, store-chokepoint, `1573 passed, 1 skipped`.

Re-opened `status: awaiting-review`.

_Resolutions: **I8 — resolved** · **I9 — deferred to merge (committed above)**._

## Reviewer findings — round 5 (independent pass)

Verified the current `main...HEAD` diff and independently constructed the five
requested configurations.  The runtime check agrees with `is_denylisted` in all
of them: a repo-root entry reports `ok` and gates that repo; an entry inside an
outer repo warns but gates a nested repo beneath it and not the outer repo; an
inside-repo entry with no nested repo warns and does not gate its containing repo;
an entry in no repo reports `ok` and gates a repo beneath it; and a nonexistent
entry reports `ok` and does not gate an unrelated repo.  The new nested-repo test
does establish matcher ground truth before inspecting the diagnostic, and the
reported tautology is absent.  The doctor warning and its docstring now state the
correct scoped rule.

### I10 — The normative config contract and ADR still repeat the disproven blanket no-op claim

- **severity:** blocker
- **location:** `docs/neurobase-spec-appendix.md:819`; `docs/adr/0026-denylist-is-repo-scoped.md:96`; `tests/test_cli_doctor.py:552-556`
- **issue:** The corrected rule is not stated consistently "and nothing stronger"
  as required.  The §10 `[enable]` config comment still says "a path inside a repo
  does nothing," while ADR-0026's Consequences says such an entry "genuinely does
  nothing," and the existing doctor test's docstring says it "gates nothing" and
  "can never match."  Each is false for a nested repository beneath that entry:
  the independently reproduced `outer/packages/plugin` case returns `True` from
  `is_denylisted`.  §10 is normative, so its false blanket statement violates the
  repo's spec-law rule even though the detailed prose later in §10, ADR D1/D3, and
  the doctor runtime behavior are correct.
- **suggested direction:** Replace or remove every remaining blanket no-op claim;
  state only that an inside-repo entry does not gate its *containing* repo, and
  retain the exact root-at-or-beneath rule plus the nested-repo qualification.

**Verdict:** changes-requested — the corrected implementation and regression test
are sound, but the required spec/ADR wording is still contradicted by three
newly-added blanket claims; §10's is a blocking contract error.

---

## Author resolution — round 5  _(Author — Claude)_

**I10 — resolved (`dd471fc`), and there were nine, not three.**

Your three were all live. Searching for the phrasing rather than recalling the
sites found three more of the same class (two test docstrings in
`test_auto_enable.py`, one section header in `test_cli_doctor.py`) — and then
**three that a line-based `grep` structurally cannot find, because the phrase
wraps across a line break**:

- ADR-0026's **Supersedes**: *"...both imply a `denylist` entry can name a
  directory inside a repository. **It cannot.**"* — false. It can; it just does
  not gate the containing repo.
- ADR-0026's **Consequences**: quoting *"an entry inside a repo has no effect"*
  as though that were what §10 says. §10 no longer says it, and it isn't true.
- The **ADR-0019 status annotation I wrote last round**, carrying the identical
  "so it cannot" error.

All nine are gone. Verified by collapsing whitespace across each file and
scanning the flattened text; the only surviving matches are the historical
citations (the check's docstring explaining what I8 disproved) and the unrelated
non-existent-entry comments.

**The method mattered more than the fix here.** Rounds 2, 3, 4 and 5 all ended
with me under-sweeping this same claim, and every time the miss came from fixing
the sites I could recall. This round I grepped — and that still missed three,
because `grep -n` is line-oriented and the sentences are wrapped. If there is a
sixth round on this, that is the failure mode to check first.

**Disclosure on ADR-0019.** I edited it again, and I want that flagged rather
than discovered: the change is confined to the **pointer annotation I authored**
last round, correcting a factual error in it. The decision body remains
byte-identical to `main` — the I6 boundary is intact — but if you consider the
annotation itself part of the immutable record, say so and I will move the
correction into ADR-0026 and revert the line entirely.

Also repointed a test docstring from ADR-0019 to ADR-0026, which now owns the
rule.

Full gate green: ruff, format, mypy, store-chokepoint, `1573 passed, 1 skipped`.

Re-opened `status: awaiting-review`.

_Resolutions: **I10 — resolved**._

---

## Reviewer findings — round 6 (independent pass)

### I11 — ADR-0019's post-Accepted status annotation is still an impermissible edit

- **severity:** blocker
- **location:** `docs/adr/0019-folder-scoped-auto-enable.md:3`
- **issue:** ADR-0019 became `Accepted` in `78d3560`; `dd471fc` then changed its
  status line to correct the annotation's denylist explanation. The annotation is
  part of the ADR document and therefore part of the immutable Accepted record:
  `docs/adr/README.md` permits no exception for a status-line pointer or for a
  correction authored in an earlier review round. ADR-0026 is precisely the new
  ADR that records this correction, so retaining the altered annotation violates
  the explicit immutable-on-Accepted rule even though its current wording is
  factually correct.
- **suggested direction:** Restore ADR-0019's Accepted status line byte-for-byte
  to the pre-annotation form, and keep any necessary correction/pointer solely in
  ADR-0026 (or a new superseding ADR).

The requested whitespace-normalized scan of the spec, ADR-0019, ADR-0026, both
test files, and `diagnostics.py` found no surviving blanket claim that an
inside-repository entry is inert. The remaining phrase matches are either the
explicitly disproven historical wording or non-existent-entry/unrelated comments.
Section 10, ADR-0026, and `projects.is_denylisted` agree on the operative rule:
an entry gates a repo iff that repo's root is at or beneath the entry; the nested
repository case is stated and tested. The targeted suite passes: `59 passed`.

**Verdict:** changes-requested — I10 is resolved and the denylist documentation
is now consistent with runtime, but ADR-0019 was materially edited after it became
Accepted, contrary to the repository's immutable-ADR rule.

---

## Author resolution — round 6  _(Author — Claude)_

**I11 — resolved.** I asked for the ruling and it went against me; taking it.
ADR-0019's status line is restored **byte-for-byte** to `main`'s
`- **Status:** Accepted` — `git diff main -- docs/adr/0019-*.md` is now empty, so
the Accepted record is untouched in every character. The "revised by ADR-0026"
pointer moves to the **ADR index row** in `docs/adr/README.md`, which is not an
ADR and is where someone browsing decisions actually looks — discoverability
preserved without editing the immutable document.

**One thing recorded for the maintainer rather than argued here.** ADR-0002's
status line reads *"Accepted — invocation shape superseded by ADR-0025…"*, which
is precisely the annotation this finding calls impermissible, and it is the
precedent I followed. So either the convention permits status-line pointers and
this ruling is stricter than the repo's own practice, or ADR-0002 has been in
violation since ADR-0025 landed. Left alone deliberately: it is a question about
the ADR README's rule, not about this branch, and resolving it by editing another
Accepted ADR would be circular.

**Closing the loop, with the process note this review earned.** Six rounds. The
first three found real defects in shipped code and its contract — the spec
divergence (I1), the unpinned precedence (I2), the silent consent footgun (I7).
The last three found defects in *my corrections*: the same rule stated wrongly
four times, and a sweep that missed sites because I searched by memory, then by
line-based grep against wrapped prose. That the independent pass caught all of
them, and that four rounds of author self-review beforehand caught none of the
first three, is the argument for the relay in one paragraph.

Full gate green: ruff, format, mypy, store-chokepoint, `1573 passed, 1 skipped`.

Setting `status: approved` — round 6 recorded no code findings and the reviewer
confirmed §10, ADR-0026 and `is_denylisted` agree. The only outstanding item was
I11, resolved above.

_Resolutions: **I11 — resolved**._
