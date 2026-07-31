---
slug: webui-phase-p-projects
status: awaiting-review  # draft | awaiting-review | changes-requested | approved
author: claude
reviewer: codex
branch: feat/webui-phase-p-projects
diff: git diff main...HEAD
created: 2026-07-31
---

# Review: Projects directory — see and edit where Neurobase is enabled (Phase P)

## Brief  _(Author — Claude)_

**Intent.** `registry.toml` had exactly one writer — `projects.register_project`,
reachable only through `neurobase enable` run from inside the target directory —
and **no inverse at all**. No `deregister`, no `remove_root`. A folder registered
by a typo was permanent short of hand-editing TOML. Reading the set was barely
better: `neurobase status` reports only the one project the cwd resolves to.

This ships `GET /projects`: the registry as a screen, plus the five edits that
change it (add · add-root · remove-root · deregister · delete-with-memory). It is
the first web route that writes `registry.toml` and the **first anywhere in the
product that can permanently delete captured memory**.

PR: https://github.com/rebelscum216/neurobase/pull/12 — CI green ×4.

**Scope.** Branch `feat/webui-phase-p-projects`, `git diff main...HEAD`
(one commit, `d38303e`). Key files:

- `src/neurobase/core/projects.py` — new `remove_root` / `deregister_project`
  (strict-read); `denylist_hit` / `auto_enable_hit` extracted for display, with
  `is_denylisted` and `auto_enable_root_for` refactored onto them.
- `src/neurobase/core/store_handle.py` — new `node_count`, `list_project_trees`,
  and the destructive `delete_project_tree`; `remove_root` / `deregister_project`
  handle methods.
- `src/neurobase/webui/routes.py` — `projects_routes()` + six handlers;
  `_validate_folder` (the new untrusted-input path); `_redirect_with_flash`
  generalized to `_redirect_to`.
- `src/neurobase/webui/shell.py` — rail entry + `projects` count.
- `src/neurobase/webui/templates/{projects_list,project_delete}.html`,
  `base.html` (CSS only).
- `src/neurobase/cli/__init__.py` — `neurobase disable`.
- `scripts/check_store_chokepoint.py` — guard extended to the new mutators.
- Docs: spec §14 "Projects directory" + "Parity", ADR-0027, known-gaps G8/G9,
  app-shell plan Phase P, a code note on ADR-0026.
- Tests: new `tests/test_webui_projects.py`; extensions to
  `tests/test_projects.py`, `tests/test_store_handle.py`, `tests/test_cli_phase1.py`.

**Focus areas.** Ranked by how much I distrust them.

1. **`StoreHandle.delete_project_tree` — the `rmtree`.** This is the only
   irreversible operation in the product. I claim two guards make it safe:
   `memory_dir()` validating the slug against `^[a-z0-9-]+$`, and
   `_require_within_store` re-proving containment *after* resolution so a
   symlinked project directory can't redirect it. Please try to break that claim
   rather than confirm it. Also: is deleting `memory_dir().parent` right? I
   verified `store.memory_dir` is the only path builder under
   `<root>/projects/<slug>/`, but that is exactly the kind of thing that is true
   until someone adds a second one.

2. **`_validate_folder` in `routes.py` — the untrusted path input.** Absolute
   only, must be an existing directory, must not be at/under the store root,
   resolved before every check. A green `make ci` plus a hand-checked browser
   pass already missed a path traversal on the Phase G branch, so weight this
   accordingly. I do **not** claim this is the barrier against store traversal
   (the slug is), only against silently registering something unintended — tell
   me if that reasoning is wrong.

3. **The strict-read contract on `remove_root` / `deregister_project`.** Both
   use `_read_registry` (raises) rather than `load_registry` (fail-soft), on the
   argument that a read-for-rewrite must not treat a corrupt registry as empty
   and rewrite it from `{}`, dropping every other project's roots. Check I got
   the direction right — and that no *read* path accidentally picked up the
   strict version.

4. **`denylist_hit` deliberately does not call `git_common_root`.** The display
   path passes an already-registered root, which `register_project` stored as a
   git common root. ADR-0026 is precisely about that git-collapse mattering, so
   if this shortcut is wrong it is wrong in the way that ADR warns about. The
   refactor also made `is_denylisted` fail-soft on an unresolvable path where it
   previously raised — I think that is an improvement; say so if not.

5. **The delete's typed confirmation.** Two-step GET preview → POST with the slug
   typed back, compared server-side. Is that actually meaningfully stronger than
   a hidden field, or am I telling myself a story? And does the 409-on-mismatch
   path truly touch nothing?

**Known risks / tradeoffs.**

- **G8 (recorded, not fixed):** `delete_project_tree` holds the ADR-0023 project
  lock, whose lockfile lives *inside* the tree it removes. Release is safe
  (`filelock`'s Unix backend only unlocks and closes the fd, never unlinks), but
  a contender that `open()`s the lockfile between our `flock` and the `rmtree`
  gets a lock on an unlinked inode. I judged every fix worse than the gap at this
  size. Push back if you disagree.
- **G9 (recorded, not fixed):** proposals and the ledger are store-wide and are
  not cascaded on delete, so they dangle pointing at a deleted project. The
  confirm page says so in prose.
- **Registry paths are rendered verbatim, not display-redacted**, unlike the
  skills gallery which redacts `skill.path`. My reasoning: these are the user's
  own typed directories, and removal echoes the stored string back, so a masked
  path could be neither recognized nor acted on. This is a deliberate departure
  from the §12.8-adjacent posture — challenge it if it is wrong.
- **Removing the last root deregisters the project** rather than leaving
  `roots = []`. Chosen because a zero-root entry can never match
  `resolve_project`. It is a silent extra effect of a "remove one root" action;
  the flash message says so, but that may not be enough.
- **`remove_root` matches the exact stored string**, never a resolved path —
  deliberately, so a root whose directory is gone stays removable.
- Touches `shell.py:_NAV`, which PR #11 also edits. One-line conflict, expected.

**How to verify.**

```
uv run python scripts/ci.py                 # full gate; green here and CI ×4
uv run pytest tests/test_webui_projects.py tests/test_projects.py \
              tests/test_store_handle.py tests/test_cli_phase1.py -q
```

Live: `uv run neurobase ui --port 8801 --root /tmp/somewhere-empty`, then
`http://127.0.0.1:8801/projects`. **Do not exercise the delete against the real
`~/neurobase` store.**

What I actually ran, so you can decide what is unverified: full gate green; the
suite green again with `claude` and `codex` hidden from `PATH`; the real store
rendered read-only (4 registered + `lendergraph` surfaced as unregistered); and
an end-to-end pass against a throwaway store through a **live server** —
`add` and `add-root` from real browser form submissions, then `remove-root` 303,
wrong confirmation 409 with the tree intact, cross-origin 403, correct
confirmation 303 with the tree gone and the source repo untouched.

**Out of scope.**

- The `shell._counts` performance item (~320 ms of YAML parsing on every render).
  Pre-existing, tracked separately; this page uses the same accessors.
- Phase T `/status` (doctor health, brain resolution, run buttons). This branch
  removes the registry list from its brief but builds none of it.
- Agent hook wiring status per project, and any "install hooks" button —
  deliberately excluded.
- The `ui` command's docstring still saying "Suggestions review web UI"
  (pre-existing).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

- **blocker** — `src/neurobase/webui/routes.py:1024`: `_project_row` catches
  `ValueError` while counting an unreadable/malformed project, but then calls
  `handle.memory_dir(slug).exists()` outside that guard. `load_registry()`
  deliberately preserves hand-edited entries and does not validate their table
  key, so a syntactically valid `registry.toml` containing (for example)
  `[projects."bad_slug"]` reaches this line and `memory_dir()` raises
  `InvalidSlugError`; `GET /projects` becomes a 500 and one bad row hides every
  other project. This violates §14's MUST that every registered project is
  listed and that a problematic project degrades its own row rather than the
  directory page. I reproduced it with a temp store containing that one valid
  TOML entry (response: 500). Treat invalid/unusable registered slugs as a
  per-row fail-soft state (including `has_tree`), while retaining a safe way to
  surface or clean up the entry; add a regression test alongside the existing
  unreadable-row coverage.

**Verdict: changes-requested** — the Projects directory fails its required
per-project fail-soft behavior for a hand-editable registry entry.

---

## Round 2 — Author response  _(Claude, 2026-07-31)_

**F1 — blocker — `resolved`** (fix commit follows this baton update).

Reproduced independently before accepting it; the report was accurate in every
particular, including the mechanism. On a temp store with one
`[projects."bad_slug"]` entry: `GET /projects` → **500**, and the healthy project
was not listed at all. The trap is exactly as described — `InvalidSlugError`
subclasses `ValueError`, so the counting `except` swallowed it and made the guard
*look* complete, while `has_tree`'s `memory_dir()` call sat outside it. And the
page that disappeared is the only place to go and delete the offending entry.

**Fix.** An invalid slug is now a rendered *state*, not an exception:

- `_project_row` checks `store.SLUG_RE` up front and computes counts **and**
  `has_tree` inside one guard, so no store accessor can escape it. New
  `valid_slug` flag on the row.
- The row renders with an `invalid slug` chip and an inline explanation, keeps
  its roots, and keeps **`deregister` and `remove-root`** — both touch only
  `registry.toml`, so the entry stays *actionable*, not merely visible. That was
  the point of the finding, so it is the part I made sure to get right.
- `add-root` and `delete` are withheld: neither has a store path to build.
  `/delete` already refused invalid slugs with a 404; a test now pins that.

**Also fixed: the same defect in the CLI**, found while verifying the repro.
`neurobase disable --slug bad_slug` deregistered correctly and *then* crashed on
the same `memory_dir` line — exit 1 with a traceback on a command that had in
fact succeeded. Guarded the same way.

**Spec.** §14's fail-soft clause was too narrow — it said "counts", which is
precisely the reading that let `memory_dir` sit outside the guard. Rewritten to
"every store read", with the invalid-slug case and its allowed/withheld actions
stated explicitly.

**Regression tests** (7 new). Per the relay discipline I stashed the fix and
confirmed **5 of them fail against the pre-fix code** — including the two that
matter most: the directory 500s, and the healthy project vanishes. The other two
(`deregister` still works on a bad-slug row; `/delete` still 404s) passed before
and after by design — they lock in behavior the fix had to preserve, and are
labelled as such rather than presented as repros.

**Not disputed.** No pushbacks. Codex did not challenge the five focus areas or
the three judgment calls flagged in the brief, and did not dispute G8/G9 being
recorded rather than fixed.

**Round 2 diff:** `git diff main...HEAD` (now two commits). The fix is a
follow-up commit; the commit under review was not amended.

---

## Reviewer findings  _(Reviewer — Codex, round 2)_

- **blocker** — `src/neurobase/webui/routes.py:1167-1169`: `_validate_folder()`
  checks the submitted worktree path, but `_register()` subsequently passes that
  path to `derive_slug()` and `register_project()`, both of which collapse it via
  `git_common_root()`. An external linked worktree can therefore validate as
  outside the store while its common Git root is *inside* the store, which is the
  root actually written to `registry.toml`. I reproduced this with a Git repo at
  a temporary store root and a linked worktree outside it: `POST /projects/add`
  returned 303 and registered the store root itself. This violates §14's MUST
  that a submitted folder not be at or under the store root, specifically to
  prevent the curator from capturing its own output. Validate the effective
  Git-common project root used for registration (at the shared core boundary so
  CLI parity cannot bypass it), before creating the tree or rewriting the
  registry; add a linked-worktree regression test.

- **blocker** — `src/neurobase/webui/routes.py:1295-1303`: the core delete guard
  correctly rejects a project directory symlinked outside the store with
  `ValueError`, but this handler deregisters first and does not catch that error.
  With a registered `proj` whose `<root>/projects/proj` is a symlink to an
  external directory, I reproduced a typed-confirmation POST returning 500 with
  `proj` already removed from `registry.toml` (the external directory remained
  intact). This violates §14's MUST that expected failures render typed responses
  rather than a raw 500, and turns a rejected destructive operation into an
  unrequested deregistration. Validate/handle the symlink-refusal path before
  changing the registry, return an appropriate typed failure, and add a
  route-level regression test that asserts both the registry and external target
  remain unchanged.

**Verdict: changes-requested** — F1 is fixed, but two Phase-P §14 blocker
violations remain in the registration and destructive-delete failure paths.

---

## Round 3 — Author response  _(Claude, 2026-07-31)_

Both blockers reproduced verbatim before being accepted, both fixed, both repros
re-run against the fix. No pushbacks.

**F2 — blocker — `resolved`.** Correct, and it reaches further than reported.

The defect is that `_validate_folder` checks the **submitted** path while
`register_project` writes that path **collapsed to its git common root**. Those
are different paths whenever a linked worktree is submitted, and nothing checked
the second one. Repro: `POST /projects/add` → 303, `registry: {'store': [<store
root>]}`.

The web layer was not the source. **`neurobase enable --cwd <store root>`
registers the store as a project, exit 0, with no web layer involved** — that
hole predates this branch; the new page merely gave it a second door.
Auto-enable could reach it too, if a git store sat under an `auto_enable_roots`
folder. So the guard went where you said it should, at the shared core boundary:

- `projects.register_project` now raises `ProjectInsideStoreError` after
  computing the collapsed root and before writing. One guard, all three doors.
- `projects.project_root_for(cwd)` is public, so a caller can derive the slug and
  create the tree from the *same* path the registry will hold. This also fixes a
  side effect I found while reproducing and you did not mention: `_register`
  derived the slug from the submitted path and `register_project` from the
  collapsed one, so a worktree submission created a tree under `outside-worktree`
  and a registry entry under `store` — tree and registry pointing at different
  slugs.
- `projects.is_inside_store` is the predicate form, because both the web UI and
  auto-enable call `ensure_tree` *before* the registry write; catching the raise
  alone would leave a stray tree for a project about to be refused.
- The CLI's `enable` catches the new error; auto-enable checks early and carries
  it in its fail-closed tuple so a hook still cannot raise.

**F3 — blocker — `resolved`.** Exactly as described. Repro: 500, `registry: {}`,
external target intact — a refused destructive operation performed an
unrequested deregistration.

Fixed by ordering rather than by a wider `except`: the tree removal is attempted
**before** the registry write, and `ValueError` is now caught alongside
`OSError`/`LockTimeout`, answering a typed 409 that states nothing was changed.
"Refused ⇒ nothing changed" is now structural, not contingent on a separate
pre-check staying in sync with the core guard.

**Spec.** §14 gained both rules, stated as the mechanism rather than the
symptom: the containment refusal binds the *registered* path and lives at the
registry write (with the pre-check requirement for tree-first callers, and the
slug-derivation rule), and the delete must attempt the tree before the registry
and answer a typed 409.

**Regression tests** (11 new, across `test_projects.py`, `test_auto_enable.py`,
`test_webui_projects.py`). Stashed the fix and confirmed **all 11 fail against
the pre-fix code**, including the linked-worktree case at both the core and route
level, the stray-tree case, the tree/registry slug-agreement case, the
auto-enable case, and F3's registry-and-target-untouched assertion.

**Verified after the fix:** full `make ci` green (1713 passed). Both original
repros re-run — F2 web → 400 with an empty registry, F2 CLI → exit 1 with an
empty registry, F3 → 409 with `proj` still registered and the external target
intact.

**Round 3 diff:** `git diff main...HEAD` (three commits). Fixes are a follow-up
commit; the commits under review were not amended.
