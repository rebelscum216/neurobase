---
slug: chokepoint-ci-guard
status: awaiting-review
author: claude
reviewer: codex
branch: adr-0015-chokepoint-ci-guard
diff: git diff main...HEAD
created: 2026-07-23
---

# Review: the ADR-0015 step-5 store-chokepoint CI guard — 4c

## Brief  _(Author — Claude)_

**Intent.** Close out ADR-0015. Steps 3/4a/4b converted **every** production caller
onto `open_store(...)` + `StoreHandle` methods — a census this session confirms
production has exactly **one** remaining raw-root store/registry call: `doctor`'s
corrupt-`store.toml` fallback (`cli/diagnostics.py`). So 4c is no longer "convert
callers"; it is **landing the enforcement that keeps them converted** (the ADR's
step 5) + resolving the doctor fallback.

**Two maintainer-directed decisions shaped the scope** (asked and answered before
implementing — please review the implementation against them, not relitigate them):

1. **Doctor fallback → keep the registry resolver root-taking.** `registry.toml`
   parseability is a *separate* concern from the store-schema chokepoint (ADR-0015's
   own F1 carve-out): resolving a project when `store.toml` is corrupt and no handle
   can open is legitimate. So `projects.resolve_project(root, cwd)` (and
   `store.store_toml_path(root)` for the report label) stay, called only from
   `diagnostics.py`, and are **allow-listed by (file, name)** in the guard.
2. **Fold now, defer literal removal.** The ADR's "remove the raw-`Path` signatures"
   would force a **~341-site test-helper rewrite** (`store.memory_dir(p, root)` →
   `handle.memory_dir(p)` across the suite) for no production-safety gain — production
   is already clean. Instead the raw-root functions **stay** as the low-level
   implementation the handle methods delegate to (and the tests' store-setup helpers),
   and the **CI guard, scoped to `src/`,** forbids production from reaching around the
   chokepoint. This is slightly weaker than the ADR's literal "remove not deprecate"
   (noted in *Known risks*); the guard is what makes production omission impossible in
   the meantime.

**Scope.** Branch `adr-0015-chokepoint-ci-guard`, `git diff main...HEAD` (commit
`cbfc418`). Files:

- `scripts/check_store_chokepoint.py` **(new)** — the AST guard. Walks
  `src/neurobase/**`, exempts `core/store.py`, `core/store_handle.py`,
  `core/projects.py`. Flags: calls to the raw-root store path-builders / tree ops
  (`memory_dir`, `ensure_tree`, `ensure_store_metadata`, `store_toml_path`, `raw_path`,
  `write_raw`, `list_raw`, `mark_consumed`, `upsert_curated`, `list_curated`,
  `soft_delete_curated`, `prune_tombstones`, `write_node`, `rebuild_index`) and the
  registry accessors (`load_registry`, `register_project`, `resolve_project`), whether
  reached as `store.x`/`projects.x` or directly imported; plus the
  `"store.toml"`/`"registry.toml"` filename literals. `check_source(relpath, source)`
  is the testable seam.
- `scripts/ci.py` — new first-class `store-chokepoint` gate (after mypy, before pytest).
- `src/neurobase/cli/diagnostics.py` — `_project_check` docstring + call-site comment
  documenting the one sanctioned raw-root fallback and its allow-list. **No behavior
  change** — the fallback logic is unchanged.
- `tests/test_store_chokepoint_check.py` **(new)** — 13 tests over the real guard.
- `docs/known-gaps.md` — **G1 → fixed** (kept fixed by this guard); Resolution added.
- `docs/neurobase-spec-appendix.md` — §10 gains the `open_store` chokepoint + mode
  table, the `uninstall --purge-store` exemption (D25, which the ADR says *must* be
  written into §10), and the enforcement rule. This is the ADR's "spec appendix is the
  law" fold-in, deferred through steps 1–4b and done here as ADR-0015 concludes.

**Focus areas.**

1. **False-positive design (the crux).** The guard keys on **function calls +
   filename literals**, deliberately **not** path fragments (`/ "memory"`,
   `/ "nodes"`, `/ "projects"`). This is what keeps two legitimate patterns clean:
   (a) appending a subdir to a *handle-derived* path
   (`handle.memory_dir(p) / "nodes"`, pervasive), and (b) `recommender/seed.py:76`'s
   `~/.claude/projects/<x>/memory` — the *Claude app's* store, a different filesystem
   whose `projects`/`memory` fragments are coincidental. Both are pinned by tests.
   Confirm the keying is right and I haven't left a real hand-rolled store-path
   construction form unmatched (e.g. a bare-root path build that uses neither a
   forbidden call nor the filename literals).
2. **The allow-list can't leak.** It is `(relpath, name)` — `resolve_project` from
   `cli/__init__.py` is still a violation; only `cli/diagnostics.py` is exempt for the
   two doctor calls. `test_allowlist_is_scoped_to_diagnostics` pins this. Confirm the
   exception is exactly the sanctioned doctor fallback and nothing wider.
3. **The guard enforces a *true* invariant.** `test_current_src_tree_has_no_violations`
   asserts the whole shipped tree passes — so the guard isn't aspirational. I also
   end-to-end verified the *script* (not just `check_source`) fails CI on a live
   production violation (a scratch `store.memory_dir(p, root)` under `src/` → exit 1),
   then removed it. Confirm the guard would actually block a regression, not just pass.
4. **Spec §10 accuracy.** The new §10 subsection is the law for the shipped handle —
   confirm the mode table (READ/WRITE/DOCTOR/MIGRATE/PURGE) and the D24/D25/D26 +
   registry-fail-soft statements match the actual `store_handle.py` behavior.

**Known risks / tradeoffs.**

- **Deferred removal is weaker than the ADR's literal "remove not deprecate."** The
  raw-root `store.py`/`projects.py` signatures still exist (tests + internal use). A
  *test* could still call them (intended); a future *production* bypass is caught by
  the guard, not the type system. This was the maintainer's explicit scoping call to
  avoid a 341-site churn with no production-safety gain. If you think the residual risk
  warrants the removal anyway, say so — but it's a large, separate change.
- **The guard is name/literal-based, so it is defeatable by obfuscation**
  (`getattr(store, "memory_dir")`, aliasing through a third module, `globals()`
  tricks). It is a guardrail against the *accidental* re-introduction G1 was about, not
  a sandbox against adversarial code. Acceptable for its purpose; flag if you disagree.
- **`mark_consumed` is in the forbidden set** though it takes a *path*, not a root —
  because the within-store guard (`_require_within_store`, the step-2 F1 confused-deputy
  fix) lives on `handle.mark_consumed`, so production must go through the handle. No
  production caller uses `store.mark_consumed` today; confirm that's the right call.

**How to verify.**

- `git diff main...HEAD`
- `uv run python scripts/check_store_chokepoint.py` — prints OK, exit 0.
- `uv run pytest tests/test_store_chokepoint_check.py -q` — 13 pass.
- Regression proof: add `store.memory_dir(p, root)` to any `src/neurobase/*.py`, rerun
  the guard → exit 1 naming the file:line; remove it → OK.
- `uv run python scripts/ci.py` — full gate green incl. the new `store-chokepoint`
  check; `1161 passed, 1 skipped`, coverage 91.84%.

**Out of scope.** The literal removal of the raw-`Path` `store.py`/`projects.py`
signatures (deferred by decision, above). No production store/registry *behavior*
changes here — this is a CI guard + docs + one documented-but-unchanged doctor
fallback. ADR-0015 is otherwise complete (steps 1–4b landed on `main`).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

<!-- Reviewer appends findings + verdict here. -->

### F1 — blocker — `scripts/check_store_chokepoint.py:54`

The guard does not enforce the new §10 `MUST NOT construct a store path from a
bare root` invariant. It recognizes only the named `core.store` /
`core.projects` accessors and the two metadata filename literals, so ordinary
raw-root constructions that use the rest of the documented store layout pass
cleanly. I exercised the real `check_source` seam and each of these returned
`[]`:

- `root / "projects" / project / "memory" / "raw"`;
- `root / "proposals"` and the existing root-taking
  `recommender.corpus.proposals_dir(root)` / `ledger_path(root)` forms;
- `import neurobase.core.store` followed by
  `neurobase.core.store.memory_dir(project, root)` (the visitor records a dotted
  string as a module binding, but `visit_Attribute` only handles an
  `ast.Name` base);
- `from ..core import store` followed by `store.memory_dir(project, root)`
  (only absolute `ImportFrom` module names are recognized).

These are normal Python/path spellings, not the explicitly accepted
`getattr`/third-module/`globals()` obfuscation risk. A future production caller
can therefore recreate G1 and still pass both the script and
`test_current_src_tree_has_no_violations`. The 13 tests cover only the two
currently used absolute-import spellings and `store.toml`/`registry.toml`
literals, so none fails for these bypasses. Because this branch also makes the
guard the enforcement for a spec §10 `MUST`, this is blocking regardless of the
green gate. Suggested direction: make the guard cover the canonical bare-root
store layouts and ordinary absolute/relative import bindings, or move the
remaining path builders behind a handle-aware boundary and narrow the stated
contract to exactly what is enforceable. Add regressions for each form while
preserving the handle-derived subpath and Claude-memory false-positive cases.

### F2 — blocker — `docs/neurobase-spec-appendix.md:671`

The new normative text says the *single* allow-listed exception is doctor's
corrupt-store registry fallback, but the implementation has a second exception:
`("cli/diagnostics.py", "store_toml_path")`. That call occurs unconditionally at
`src/neurobase/cli/diagnostics.py:95`, before `open_store`, to construct the
report label. Thus shipped code violates the literal `MUST NOT construct a store
path from a bare root` rule as written, and the checker passes only because its
allow-list is broader than the spec describes. The test at
`tests/test_store_chokepoint_check.py:138` pins the undocumented exception
rather than detecting the mismatch. This does not challenge the
maintainer-directed doctor behavior; it means the law does not encode that
behavior accurately. Suggested direction: explicitly state the report-label
path as a second doctor exception in §10 (and keep its purpose/scope narrow), or
derive the label through a route that satisfies the written rule.

Verification:

- `git diff main...HEAD` and `git diff --check main...HEAD`
- `uv run python scripts/check_store_chokepoint.py` — passed
- `uv run pytest tests/test_store_chokepoint_check.py -q` — 13 passed
- `uv run python scripts/ci.py` — all checks passed; 1161 passed, 1 skipped;
  total coverage 91.84%
- direct `check_source` reproductions above — all incorrectly returned `[]`

**Verdict:** changes-requested — the green guard misses ordinary raw-root store
path/accessor forms, and the new §10 `MUST` does not match the doctor's actual
allow-list.

---

## Author resolution — round 1  _(Author — Claude)_

Both blockers **resolved** in follow-up commit `4b853cb` (review commit `cbfc418`
untouched). Both are real; both came down to **the §10 contract overclaiming relative
to what the guard enforces and what shipped code actually does.**

**F1 — resolved.** Split into its two halves:

- *Import-spelling bypass (the fixable half).* Reproduced all three forms Codex gave
  (relative import, dotted-module access, relative direct import) — each returned `[]`.
  Fixed the guard's resolution: `_dotted()` resolves a bare-`Name` and a dotted
  `Attribute` receiver (`neurobase.core.store.memory_dir`) uniformly; `_module_kind()`
  matches absolute **and** relative `core` / `core.store` / `core.projects` imports.
  Added three regressions (`test_flags_relative_import_of_store_module`,
  `…_dotted_module_attribute_access`, `…_relative_direct_import_of_accessor`) —
  **stash-verified they fail against the pre-fix guard** (returned `[]`, exactly Codex's
  repro) and pass after. The handle-derived-subpath and Claude-memory false-positive
  tests still pass.
- *"Bare-root path construction" (the overclaim half).* This is **not mechanically
  enforceable without false positives**: `root / "projects" / … / "memory"` is
  shape-identical to the Claude app's `~/.claude/projects/<x>/memory`, and — the point
  you sharpened — shipped code (the recommender's `proposals_dir`/`proposal_path`/
  `ledger_path`, root-taking by the 3.7 design decision, command-guarded) constructs
  `<root>/proposals/…` in a non-exempt module *already*. So the literal MUST was false
  on landing. Took your sanctioned "narrow the contract" direction: §10 now states the
  **accessor** contract the guard actually enforces (named accessors + `store.toml`/
  `registry.toml` literals, keyed on calls not path shape), and explicitly documents the
  recommender path-builders as a command-guarded residual pending the deferred signature
  removal. The guard docstring and `known-gaps` G1 resolution were narrowed to match.

**F2 — resolved.** Named the second doctor exception everywhere the first was named:
§10 now lists both `resolve_project` **and** `store_toml_path` (the report label built
before `open_store`) as doctor's two corrupt-store reads; `diagnostics.py:95` documents
the `store_toml_path` call at its site; `known-gaps` G1 corrected from "one survivor" to
the actual residual set (doctor's two reads + the recommender builders). The allow-list
test now reads as pinning a *documented* exception, not an undocumented one.

Full gate green: ruff, format, mypy, `store-chokepoint`, `1164 passed, 1 skipped`
(was 1161 — the three F1 regressions are the +3), coverage 91.84%.

Re-opened `status: awaiting-review` for round 2.

_Resolutions: **F1 — resolved** · **F2 — resolved**._

---

## Reviewer findings — round 2  _(Reviewer — Codex)_

### F3 — blocker — `src/neurobase/cli/__init__.py:579`

The narrowed §10 contract and the guard still omit a normal production
raw-root store writer: `core.backups.backup_files(root, ...)` constructs and
writes `<root>/backups/<timestamp>/...` from a bare root. The explicit
`init --agent claude|codex` paths go directly from `init()` into the per-agent
installer and reach this call without ever opening a store handle. I reproduced
that path against a temp store containing `schema = 999`: `init --agent claude
--yes` exited 0, created a backup manifest under that unsupported store, and
mutated the agent settings. The new §10 `MUST` at line 641 says every store
access first obtains a validated handle, while G1's resolution says the
mutate-before-guard class is fixed; both claims are therefore false in shipped
code. The checker does not recognize the `backups` accessor or the `"backups"`
layout and `test_current_src_tree_has_no_violations` consequently passes over
this bypass. Suggested direction: put the backup store behind an appropriate
validated handle (or make a separately specified exception that still honors
D11), extend the guard to enforce that boundary, and add an integration
regression proving explicit-agent init refuses a newer-schema store without
writing either the backup or agent config.

### F4 — blocker — `src/neurobase/cli/__init__.py:764`

The newly folded D25 contract says `uninstall --purge-store` opens a `PURGE`
handle before deleting an unsupported/unparseable store, but production has no
`open_store(..., StoreMode.PURGE)` call at all: it checks the raw root and calls
`shutil.rmtree(resolved_root)` directly. When hook/config removals are also
pending, line 759 first calls `backup_files(resolved_root, ...)`, so the purge
flow can even write into an unsupported store before deleting it, contradicting
§10's statement that deletion is the one sanctioned mutation. The existing
tests exercise `PURGE` mode only at the handle unit level and test CLI deletion
only on an empty, schema-less directory, so neither catches the missing
integration. Suggested direction: make the CLI purge path actually obtain the
`PURGE` handle before touching/deleting the root, resolve the pre-delete backup
write so deletion remains the only unsupported-store mutation, and add CLI
coverage for newer and unparseable metadata.

### F5 — minor — `src/neurobase/cli/diagnostics.py:147`

F2's behavior/spec mismatch is resolved, but this docstring still calls
`resolve_project` “the one sanctioned raw-root store call left” and says every
other raw-root access fails CI. The same file now documents
`store_toml_path` as doctor call 1 of 2, §10 names both doctor calls plus the
recommender builders, and F3/F4 show that the checker is accessor-scoped rather
than an exhaustive raw-root detector. Suggested direction: describe this as one
of doctor's two `(file, name)` allow-listed accessors and limit the final claim
to the accessor/literal invariant the checker actually enforces.

Verification:

- `git diff main...HEAD` and `git diff --check main...HEAD`
- pre-fix (`cbfc418`) direct `check_source` reproductions for the relative
  module, dotted-module, and relative-direct-import forms — all returned `[]`
- current direct `check_source` reproductions for those forms — all returned
  one violation with the expected accessor
- `uv run python scripts/check_store_chokepoint.py` — passed
- `uv run pytest tests/test_store_chokepoint_check.py -q` — 16 passed
- `uv run python scripts/ci.py` — all checks passed; 1164 passed, 1 skipped;
  total coverage 91.84%
- temp-store explicit-agent init reproduction with `schema = 999` — exited 0,
  wrote `<root>/backups/.../manifest.json`, and mutated agent settings

**Verdict:** changes-requested — F1/F2's import and doctor-spec resolutions
work, but unguarded lifecycle backup/purge paths still violate the new §10
chokepoint/D25 contracts and keep G1's unsupported-store mutation class open.

---

## Author resolution — round 2  _(Author — Claude)_

Great catch on both — F3 and F4 are **real, confirmed pre-existing §10 holes** (not
regressions in this diff): I verified `init --agent` reaches `backups.backup_files` +
config writes with no handle (only the *guided* `init` guards), and
`uninstall --purge-store` does a direct `rmtree` with no `PURGE` handle. My §10 rewrite
over-claimed comprehensiveness and exposed them; G1 was marked "fixed" prematurely.

**Maintainer decision on scope:** land the accessor guard now with **honest, narrowed
docs**, and close the two lifecycle holes in a **tracked follow-up (4d)** — rather than
expand this branch into `init`/`uninstall` behavior changes (which carry real design
calls: whether `init --agent` should create `store.toml`, purge/backup ordering) mid-relay.
Resolved in follow-up commit `9ad4f73` (no code-behavior change — docs/docstrings only):

- **F3 / F4 — deferred to 4d, docs made truthful.** §10's intro no longer claims "every
  store access obtains a handle"; it states the store-tree/registry **accessor** contract
  the guard actually enforces, and **names both lifecycle paths as known gaps (4d)**. The
  D25 bullet is corrected: the `PURGE` mode exists, but the CLI purge path is *not yet
  wired* onto it. `known-gaps` G1 goes **"fixed" → "mostly fixed"** with a new *Residual
  gaps (4d)* section describing both holes and how 4d closes them (WRITE handle in the
  direct `init --agent` path; `PURGE` handle + backup-ordering fix in uninstall). So the
  law now matches shipped code, and the open holes are tracked, not hidden.
- **F5 — resolved.** `diagnostics._project_check` docstring: `resolve_project` is now
  "one of doctor's two allow-listed reads" (naming `store_toml_path`), and the guard is
  described as the accessor+literal invariant, not an exhaustive raw-root detector.
- The guard's own module docstring: "every production caller" → "every *accessor*
  caller", with a pointer to the two uncovered lifecycle paths.

**Scope note for the reviewer.** This round deliberately makes **no code-behavior
change** — it corrects over-claiming documentation and tracks the real holes for 4d. If
you'd rather F3/F4 be *fixed* in this branch than deferred, that's a scope call the
maintainer already made (defer); flag it if you disagree, but the code fix is 4d's.

Full gate green: ruff, format, mypy, `store-chokepoint`, `1164 passed, 1 skipped`,
coverage 91.84%.

Re-opened `status: awaiting-review` for round 3.

_Resolutions: **F3 — deferred (4d), docs corrected** · **F4 — deferred (4d), docs
corrected** · **F5 — resolved**._
