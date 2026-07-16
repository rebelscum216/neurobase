# Known gaps

Known **defects and inconsistencies in shipped code** — places where what the code
does and what it should do have drifted apart, but the fix hasn't landed yet.

This file exists because nothing else in `docs/` was the right home for it:

| If it's… | It goes… |
|---|---|
| A decision (spike outcome, D-table change) | [`adr/`](adr/README.md) — immutable once accepted |
| Scratch thinking, an investigation log | [`notes/`](notes/README.md) |
| A code-review baton | [`reviews/`](reviews/README.md) |
| A **future feature** | build-plan [Backlog](neurobase-build-plan.md) — roadmap, not a defect |
| A **defect in code that already shipped** | **here** |

## Conventions

- One `### G<n>` entry per gap, newest last. Never renumber.
- `status`: `open` · `fixed` (link the commit/PR) · `wontfix` (say why) ·
  `promoted` (moved to a GitHub issue once Phase 9 ships issue templates).
- Absolute dates (`2026-07-12`), never "recently."
- A gap is not a TODO. If it's merely unbuilt, it belongs in the build-plan
  backlog. This file is for code that is **wrong or inconsistent right now**.
- **Graduation path:** Phase 9 ships CONTRIBUTING + issue templates, at which
  point GitHub Issues becomes the tracker. Each open gap here becomes an issue;
  this file then either retires or stays as the offline, greppable mirror (agents
  can read it without network access — which is the whole point of a local-first
  project).

---

### G1 — the D11 store-schema guard is enforced per-command by hand, not at the store boundary

- **status:** fixed (ADR-0015 — the `StoreHandle` chokepoint; migration steps 1–5 +
  4d, `docs/reviews/2026-07-2*-*handle*.md`, `*-lifecycle-guards.md`). Every command that
  touches **schema-versioned store content** (`memory/`, `registry.toml`) now runs the
  D11 guard: the store-tree/registry **accessor** class is closed and CI-enforced by
  `scripts/check_store_chokepoint.py`, and the lifecycle commands open the appropriate
  handle command-side (guided `init` = `WRITE`; `init --agent` = `READ`; `uninstall
  --purge-store` = `PURGE`). The config-backup facility is a schema-independent
  maintenance exception (opaque config copies; its non-purge-uninstall/`--restore-backup`
  callers open no handle — see *Resolution* / *Residual gaps* below). Step 4d closed the
  last two paths.
- **severity:** major — spec §10 says *"refuse to **operate** on a schema newer
  than the binary."* No read-only exemption exists in the contract. At least one
  path **mutates** a newer-schema store before the guard runs, which is the exact
  outcome D11 exists to prevent.
- **found:** 2026-07-12 by Codex (how-it-works review); scope corrected across
  three rounds of the [known-gaps review](reviews/README.md) — see *Provenance*
  below, which is itself part of the finding.

**Root cause.** `store.ensure_store_metadata()` — the guard — has exactly **five
call sites**:

| Call site | Reached by |
|---|---|
| `core/store.py:118` (inside `ensure_tree`) | anything that creates a tree |
| `cli/__init__.py:57` (`_check_store_schema`) | the CLI commands that remember to call it |
| `adapters/recall_common.py:81` (`build_context`) | session-start recall |
| `adapters/claude/scribe.py:171` | Claude capture |
| `adapters/codex/scribe.py:245` | Codex capture |

Nothing enforces the guard *at the store boundary*. `store.memory_dir()` and
`projects.load_registry()` will happily read or write a store of any schema. So
protection is opt-in per command, and a path is protected only if its author
remembered — which several did not, and which nothing prevents the next author
from forgetting.

**Confirmed defects.** Verified against source; these are examples of the root
cause, not an exhaustive census (see *Provenance*):

1. **`init` (guided) mutates before it guards — the worst case.** `_init_guided`
   calls `projects.register_project()`, which reads *and unconditionally rewrites*
   `<root>/registry.toml` (`core/projects.py:101`, `_write_registry`), and only
   *then* calls `store.ensure_tree()`, where the guard finally runs. A
   newer-schema store is **written to** before it is ever checked.
2. **`mcp serve` never guards at all.** `mcp/server.py` contains no call to
   `ensure_store_metadata`. `build_server()` resolves the project from
   `registry.toml` at startup, and `memory_search`, `memory_read_node`,
   `memory_list_projects`, `recommendations_list`, and (when
   `[mcp] expose_resources` is on) the node resources all read store state with no
   check. `memory_remember` is the partial exception: its *write* is guarded via
   `ensure_tree`, but reads precede it.
3. **`status --recommender` returns before the guard.** `status()` branches on
   `recommender` and returns through `_print_recommender_metrics()` *before*
   reaching its `_check_store_schema()` call. It reads `proposals/` and the ledger
   unconditionally, and — via `metrics._recurrence_reduction` →
   `corpus.load_corpus` — `registry.toml`, `curated/`, and `raw/` as well.
4. **`uninstall --purge-store` deletes an unguarded store.** `shutil.rmtree(<root>)`
   with no schema check. (Arguably *correct* — see the fix constraints.)
5. **Pre-guard registry reads are pervasive.** `projects.resolve_project()` reads
   `registry.toml` and runs first in most flows — including `status`, `curate`, and
   every hook — so even "guarded" commands typically touch the store before the
   guard. `enable`'s own comment (`# before registry.toml is touched`,
   `cli/__init__.py:76`) shows registry reads were *intended* to sit behind the
   guard.

**Provenance — and a caution about this entry.** The first three versions of G1
each made a confident coverage claim, and each was wrong: it called the gap unique
to `status --recommender` (it isn't), then bounded the severity with a
read-only-exemption rationale that **is not in the spec** (invented), then shipped
an "exhaustive 39-entry-point audit" whose tiers didn't reconcile with their own
counts and which misfiled the `init` mutation above as a benign read. The lesson is
recorded here deliberately: **a hand-maintained census of call paths is the wrong
artifact** — it is unverifiable in review, it rots on the next commit, and three
attempts produced three wrong tables. If exhaustive accounting is ever genuinely
needed, it must be a **committed, runnable enumerator** (with a stated definition of
"entry point"), not prose. Until then this entry deliberately claims only the root
cause and named, individually-verified defects.

**Fix direction.** Do not patch call sites one by one — that is the process that
produced this. The decision is architectural and comes first:

1. **Centralize the guard at the store boundary** (recommended). Make it impossible
   to touch the store without it — enforce inside `store.memory_dir()` /
   `projects.load_registry()`, or introduce a single `open_store(root)` handle every
   path must obtain. A future omission then becomes a type error, not a silent hole.
2. **Keep it per-command** and add the missing calls. Cheaper today; re-arms the
   same footgun tomorrow.

Constraints any fix must respect:

- **MCP cannot hard-fail at startup.** Spec §13 requires `resources/list` to always
  answer with a valid array and never error (Codex drops a server that errors
  there), so an MCP refusal must surface as a **structured tool error**.
- **`uninstall --purge-store` probably *should* be exempt.** Deleting a store you
  cannot parse is the safe escape hatch *from* a newer-schema store. If that
  exemption is wanted, write it into spec §10 explicitly rather than leaving it
  implicit.
- **`doctor` must keep reporting rather than refusing** — that is its job. Note it
  currently *re-implements* the schema comparison inline (`cli/diagnostics.py`)
  instead of reusing the guard; collapse that duplication when centralizing, or the
  two will drift.

**This needs an ADR** — either route changes the contract (exemptions) or the
architecture (a store chokepoint). It cannot be settled by quietly editing code.

**Resolution (ADR-0015).** Fix-direction 1, in the strongest form: a single
validated `StoreHandle` every path must obtain via `open_store(root, mode)`, which is
the one place the D11 comparison lives. Landed as five reviewed migration PRs — (1)
`store_handle.py` + `open_store`; (2) handle methods; (3) every production module
converted onto the handle; (4a/4b) the deferred `search`/`linkify` and
`distill`/`locks` edges; (5) this CI guard; (4d) the two lifecycle guards. All three
constraints above are honored: MCP surfaces a **structured tool error** (D24), `doctor`
opens a read-only `DOCTOR` handle instead of re-implementing the comparison (D26), and
`uninstall --purge-store` opens a `PURGE` handle before deleting (D25 — wired in 4d,
which also made `init --agent` open a `READ` handle before installing hooks). The
pre-guard registry-read
pattern can no longer compile — `resolve_project`/`load_registry` production callers
go through the handle. The step-5 guard forbids the raw-`root` store/registry
**accessors** and the `store.toml`/`registry.toml` literals outside the three
implementation modules. Three documented raw-`root` residuals remain outside that
accessor coverage (none an unguarded write to schema-versioned content — spec §10):
`doctor`'s two corrupt-`store.toml` reads (`resolve_project` + `store_toml_path` in
`cli/diagnostics.py`, `registry.toml`/label reads independent of the store-schema guard,
allow-listed by (file, name)); the recommender's `proposals`/`ledger` path-builders
(`corpus.proposals_dir`/`proposal_path`/`ledger_path`), command-guarded; and the
config-backup facility (`backups.backup_files`/`restore_backup`), a schema-independent
maintenance exception (opaque config-file copies to/from `<root>/backups/`, safe on any
schema). The literal removal of the raw-`Path` `store.py`/`projects.py` signatures
(they remain the low-level implementation the handle methods delegate to, and the test
suite's store-setup helpers) is deferred; the CI guard is what makes production
accessor-level omission impossible in the meantime.

**Residual gaps — CLOSED by ADR-0015 step 4d** (`docs/reviews/2026-07-23-lifecycle-guards.md`).
The step-5 review (Codex, round 2) found two lifecycle paths the accessor conversion
never reached — the same mutate-before-guard *class* as the original G1, narrower in
blast radius. Both are now closed:

1. **`init --agent claude|codex`** — the direct per-agent installers now open a `READ`
   handle at their entry, before any backup/config write, so a store whose schema is
   newer than we support is refused before hooks are installed (`READ`, not `WRITE`:
   installing hooks must not *materialize* a store, but must still refuse an unsupported
   one). Pre-4d only the *guided* flow guarded.
2. **`uninstall --purge-store`** — now opens a `PURGE` handle (which never refuses, so
   purge works on any schema) before `shutil.rmtree`, and **skips the config backup when
   purging** so nothing is written into the store before its deletion — restoring D25's
   "deletion is the one sanctioned mutation of an unsupported store".

Each is pinned by a `schema = 999` integration regression (stash-verified to fail
pre-4d). The config-backup facility itself (`backups.backup_files`/`restore_backup`)
stays root-taking by design — it copies agent-config files *verbatim* (never touching
`memory/`/`registry.toml`), so it is safe on a store of any schema, which
uninstall/recovery **require**. Where D11 matters for these commands is installing hooks
(`init --agent` = `READ`) and deleting (`uninstall --purge-store` = `PURGE`, which also
skips the backup); the **non-purge uninstall and `--restore-backup` paths open no
handle** — the backup/restore itself is a schema-independent maintenance exception
(spec §10).

---

### G2 — accepted-proposal state can drift from disk; there is no revert path

- **status:** open
- **severity:** minor — nothing corrupts, but the store's view of the present is
  wrong: a proposal stays `accepted` with a dangling `installed_path` after its
  artifact is removed by hand, and §12.7 makes reject-on-accepted a hard error,
  so there is no sanctioned way back.
- **found:** 2026-07-16 by Andrew (live browser smoke of Web UI Phase 1 —
  accepted `surgical-git-staging`, then deleted the installed SKILL.md; the
  proposal still reads `accepted`).

**Detail.** Accept records `status: accepted`, `installed_path`, and a ledger
`accepted` event with `installed_hash` (ADR-0011). The survival metric already
distinguishes missing/modified artifacts, but `recommend list`/`show` and the
web UI render `accepted` with no drift signal, and no command un-accepts. The
ledger is honest about history; the proposal frontmatter is wrong about now.

**Direction.** Either a consent-gated `recommend revert <slug>` (flips status,
appends a ledger event, never deletes the artifact itself) or drift annotation
on every read surface (the app-shell plan's Phase S gallery renders
`installed` / `missing on disk` / `modified` honestly — but rendering is not a
fix). Needs a small §12 note either way.

---

### G3 — the skill emitter can double frontmatter and misuses `candidate_type` as `description`

- **status:** open
- **severity:** minor — the installed artifact is valid enough for Claude to
  load, but reads wrong.
- **found:** 2026-07-16 by Andrew (same live smoke; emitted SKILL.md had two
  frontmatter blocks and `description: repeated-correction`).

**Detail.** `_skill` (`recommender/emitters.py:65-86`) wraps the managed draft
in a generated frontmatter block (`name`, `description`, `neurobase_slug`,
`neurobase_managed`) and prepends a `# <slug>` heading when the draft lacks one.
A draft that itself begins with `---` frontmatter is embedded verbatim →
doubled frontmatter, with consumers reading only the first block. And
`description:` is set to `candidate_type` (`emitters.py:80` —
`str(doc.get("candidate_type") or title)`), so skills ship with descriptions
like `repeated-correction`. Partly a fixture-authoring artifact (drafts should
not carry frontmatter), but the emitter should not depend on that.

**Direction.** Emitter-side: strip or merge a draft's own frontmatter; derive
`description` from the proposal title or rationale, falling back to
`candidate_type` only as a last resort. Test with a frontmatter-bearing draft.
