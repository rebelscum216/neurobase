---
slug: folder-scoped-auto-enable
status: awaiting-review
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

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

_(none yet)_

**Verdict:** _pending_
