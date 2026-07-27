# ADR-0023: Per-project store write lock — serialize mutating passes, close the prune TOCTOU

- **Status:** Proposed
- **Date:** 2026-07-20
- **Resolves:** `known-gaps.md` **G4** (no project-level store lock; concurrent
  curate passes can interleave store mutations) — including its embedded
  `prune_tombstones` TOCTOU.
- **Supersedes:** none

> **Re-derived on current `main` (2026-07-24).** This decision was first written
> as ADR-0017 on `feat/store-write-lock-adr`, which reached APPROVED over eight
> Codex rounds — but against the pre-`StoreHandle` store API, 116 commits behind.
> Rather than reconcile that divergence the decision was **re-implemented on
> current `main`**, exactly as provenance Slice B was ([ADR-0022](0022-curator-fold-journal.md)).
> The decision below is unchanged and the reasoning transferred verbatim; the
> *wiring* is new, and **the old approval does not carry** — this branch was
> reviewed from round 1. Renumbered 0017 → 0023 because origin claimed 0017 for
> the egress-policy gate. The retired branch is kept unmerged as the content
> record; do not merge it.
>
> **One API change from the original.** `project_lock` now takes the project's
> **memory directory**, not a `(root, project)` pair, and `write_raw_guarded`
> takes the caller's `StoreHandle`. On the pre-chokepoint API the lock module
> resolved `memory_dir(project, root)` itself; under ADR-0015 that would be a
> second, unvalidated way to name a store path — the exact hole the chokepoint
> closes — and would have required a CI-guard exemption. Taking the already-
> validated directory keeps the lock a path-level primitive and the guard
> exemption-free.

## Context

Nothing serializes mutations to a project's store. `SessionStart` launches a
**detached `curate --if-stale`** for the active project
(`adapters/recall_common.spawn_curate_if_stale` →
`subprocess.Popen(..., start_new_session=True)`), and the staleness gate and the
curate pass are two separate steps (`cli/__init__.py`: `is_stale(...)` then
`run_curate(...)`), so a second launch can pass the gate while the first has not
yet consumed its raws. The mutating primitives in `core/store.py` —
`upsert_curated`, `soft_delete_curated`, `mark_consumed`, `prune_tombstones` —
therefore run concurrently against the same files. The seed importer
(`recommender/seed._import_tree`) and the MCP `memory_remember` path
(`mcp/server.py`) reach the same primitives, and **both scribes**
(`adapters/claude/scribe.py`, `adapters/codex/scribe.py`) write raws through
`store.write_raw()`. (Line numbers are omitted deliberately — refer by symbol;
they rot on every edit. P2-DOCS-PLAN-ACCURACY-001.)

This costs, concretely (from the G4 finding, `P1-DATA-INTEGRITY-003`, with a
deterministic interleaving probe):

- **Duplicated fold work** — two passes read the same unconsumed raw and both
  plan against it; `mark_consumed` is one-way, so the loser's writes still land.
- **Interleaved supersession** — an upsert and a soft-delete of the same slug
  interleave so the tombstone holds pre-upsert content while the active file
  holds post-upsert content.
- **Lost captures at the raw boundary** — a scribe overwrites a session-keyed raw
  while a plan is in flight; an unguarded `mark_consumed` then marks the *newer*
  body consumed although only the older one was planned, and the new capture is
  never folded.
- **Blocked reclamation** — an active fact with a same-slug tombstone is
  indistinguishable on disk from the midpoint of an in-flight
  `soft_delete_curated`, and `.tombstones/<slug>.md` is a **shared path** a racing
  delete can atomically replace. So no write path may delete a "stale" tombstone
  (spec §1 forbids it outright), and `prune_tombstones` carries a live **TOCTOU**:
  it reads `tombstoned_at`, decides the entry is past grace, then unlinks the
  *path* — a soft-delete can replace that path with a fresh in-flight tombstone in
  between; prune deletes the replacement, the soft-delete unlinks the active file,
  both report success, and neither copy remains.

The Slice B review implemented and reverted two "resolve the residue" variants,
each caught by a probe. The durable lesson: **with no project-level ownership,
don't delete — resolve on read.** Spec §1 now does exactly that and defers the one
remaining unowned deletion — prune — to this ADR. The store's no-loss property
therefore holds today **only for a single mutating process.**

A lock is a contract change, not a quiet edit. It must define the boundary, cover
**every** mutating entry point, pick a mechanism that works on the 3-OS CI matrix
(`ubuntu-latest`, `macos-latest`, `windows-latest`), and say what a blocked
detached freshness pass does. This is the prerequisite for provenance **Slice A**:
its `.tombstones/` reader must not inherit a race it cannot fix locally.

## Decision

Introduce a **per-project advisory write lock**. Every pass that mutates a
project's store holds it for the duration of the pass; **readers never take it.**

**Boundary — per-project.** All mutations are scoped to
`memory_dir(project, root)` = `<root>/projects/<project>/memory/`; different
projects touch disjoint trees. The lock is the tightest scope that still
serializes every conflicting mutation, and it lets two different projects curate
concurrently (a `SessionStart` in project A must not block project B). The
lockfile lives at `<memory_dir>/.lock` — a dotfile at the memory-dir root, outside
every store glob (`curated/*.md`, `.tombstones/*.md`, `nodes/*.md`), so it is
invisible to readers by construction. The shared `<root>/registry.toml` is a
*different* resource (project registration, G1's territory) and is **out of
scope** here.

**Mechanism — ownership bound to an open OS handle, via `filelock`.**
`filelock.FileLock` selects `fcntl.flock` on POSIX and `msvcrt.locking` on
Windows. The kernel — not this codebase — decides who holds the lock, and it
**releases automatically when the holding process dies.**

This reverses an earlier draft of this ADR, and the reversal is the most important
thing recorded here. That draft hand-rolled ownership: an `O_EXCL` lockfile
carrying a token, plus heartbeats and a stale-takeover protocol, chosen to avoid a
dependency and to keep one code path across the matrix. Codex's review
(the retired branch's review, P1-DATA-INTEGRITY-001 / P1-CORRECTNESS-003) demonstrated by forced interleaving
that **check-the-token-then-mutate-the-path is not mutual exclusion**:

- a stale holder's `heartbeat()` could pass its token check and then `os.replace`
  **over a successor's lock**;
- a stale `release()` could pass its token check and then **unlink a successor's
  lock**;
- a reclaimer could judge a token stale, watch the holder refresh, and unlink it
  anyway;
- and an empty lockfile — `O_EXCL` publishes the file *before* the payload write —
  was judged reclaimable but never actually reclaimed, wedging `acquire()` in a
  loop that reached neither its deadline nor its non-blocking raise.

The "the victim recovers on its next `heartbeat()`" argument the draft relied on
was therefore false: a holder can lose its token *after* its last check and before
its mutation. Binding ownership to a handle deletes that entire class of bug
**along with the token, heartbeat, staleness, and takeover machinery that produced
it.** The lesson generalizes: do not hand-roll a concurrency primitive to save a
dependency.

**Killed holders need no policy.** Because the OS drops the lock when a process
dies, there is no stale-lock timeout, no PID-liveness probe, and no takeover race
to get right. G4 asked for a stale-lock policy; the correct answer turned out to be
a mechanism that makes the question moot. One boundary, noted in review: `filelock`
falls back to a *soft* lock if the platform primitive is unavailable (e.g. `flock`
returning `ENOSYS` on some network filesystems), and a soft lock does **not**
auto-release on death. The guarantee above therefore holds on filesystems that
support the platform hard-lock primitive — which covers the local stores this
project targets, but is not unconditional.

**One handle, every store mutator.** A single context manager
`core.lock.project_lock(memory_dir, *, blocking=True, timeout=...)`, where
`memory_dir` comes from the caller's validated handle
(`handle.memory_dir(project)`). Every mutating pass wraps its work in it —
acquired **once around the whole pass**, not per primitive:

| Pass | Site | Mode |
|---|---|---|
| Curator pass (`run_curate`: consume → plan → upsert → soft-delete → **prune**) | `curator/engine.run_curate` | blocking |
| MCP `memory_remember` (single upsert) | `mcp/server.memory_remember` | blocking |
| Seed import (batch of upserts) | `recommender/seed._import_tree` | blocking |
| Detached `curate --if-stale` (background freshness) | `cli` passes `blocking_lock=False` | **non-blocking** |

Holding it once around the whole curator pass is what makes `upsert_curated` and
`soft_delete_curated` unable to interleave, and — because `prune_tombstones` is
called only inside `run_curate` (`curator/engine._curate_unlocked`, its sole call
site, confirmed in review) — **the prune TOCTOU closes structurally**: no soft-delete
from another pass can replace a tombstone path mid-prune, because no other pass
can be mutating this project at all. No rename-to-claim or second-timestamp check
is needed, and we deliberately do **not** build one. (This holds once the wiring
lands; it is a property of the decision, not of today's tree.)

**The raw boundary: scribes acquire non-blocking and fall back to a fresh
filename.** Scribes (`write_raw`) run in agent hooks, which must stay inside the
ADR-0003 latency budget and exit 0 — so they can neither block on the lock nor
fail when it is held. But they must not overwrite the raw a pass is mid-consume
on. So a scribe attempts the lock **non-blocking** (`core.lock.write_raw_guarded`):
winning it proves no pass is in flight and the ordinary session-keyed overwrite is
safe; losing it means a pass may be consuming this very file, so the capture is
written to a *fresh* filename (`write_raw(unique=True)`, which claims the name with
`O_CREAT | O_EXCL`) and the contended path is left untouched. The hook returns
immediately either way and no capture is ever lost. This extends a fallback the
Codex scribe already used for `RawConsumedError` — a fresh capture under a new
filename, per spec §1's mutability rule.

`mark_consumed`'s `expect_digest` is therefore a **defensive guard, not a
compare-and-swap**, and the correctness comes from the lock. An earlier revision of
this ADR claimed the digest check itself closed the race; Codex disproved that by
injecting a scribe write between the check and the write, after which the stale
body was written back with `consumed: true` and the newer capture was gone
(P1-DATA-INTEGRITY-002, round 2). A check and a write in two syscalls is not an
atomic swap — the same shape this ADR rejects for the lock itself. The guard is
retained only against a writer that bypasses the advisory lock.

**Blocked detached `curate --if-stale` skips, never queues.** It is a background
freshness pass; if the lock is held, freshening is already underway. Its child
acquires **non-blocking** and, on contention, exits `0` silently. User-intent
operations — explicit `neurobase curate`, `memory_remember`, seed import — acquire
**blocking with a timeout** and surface a clear error if it expires; they must not
silently no-op on a user's request.

## Consequences

- **G4 closes on landing** (status → `fixed`, link the commit): duplicated fold
  work, interleaved supersession, the prune TOCTOU, and the raw-capture loss are
  all addressed, and the no-loss property extends from single-process to
  **concurrent mutators**. Update the G4 entry and the spec §1 "no-loss holds for
  a single mutating process" caveat when the wiring merges.
- **Spec §1 gains the lock as contract.** Every mutating pass holds the
  per-project `.lock`; readers never take it; `prune_tombstones` runs only under
  the lock. Spec §1's raw section gains the contended-scribe rule: a scribe that
  cannot take the lock writes a *fresh* canonical raw filename rather than
  overwriting the one being consumed. Made when the code lands.
- **A new runtime dependency: `filelock`.** A real cost against the project's
  dependency-free instinct, accepted deliberately — the alternative was
  hand-rolling cross-platform mutual exclusion, which this review proved we get
  wrong, on a platform (Windows) the author cannot test locally.
- **Reads stay lock-free.** Recall, MCP reads, and node/index reads take nothing,
  so the ADR-0003 hook-latency budget is untouched and the `resources/list`
  never-error contract (spec §13) is unaffected.
- **Scribes never *block*, but they do participate.** They take the lock
  **non-blocking** and fall back to a fresh filename on contention — so hook
  latency is unchanged and no capture is lost. They are not lock-free.
- **Raw ordering is by the authoritative `captured_at`; uniqueness never moves the
  event time.** The root cause under P1-CORRECTNESS-006 was that `list_raw` derived
  order from *filename string-sort*, so every attempt to make a contended capture's
  filename unique did so by fabricating a later timestamp — and a fabricated time
  inverts against another session's real one. Fixed at the root in two parts:
  (a) `list_raw` sorts by the parsed `captured_at` frontmatter (the authoritative
  field), with the generation compared **numerically** as a stable within-instant
  tiebreak (`__g10000` after `__g9999`; a lexical filename sort would invert them),
  so ordering no longer depends on `agent`/`sid8` bytes; (b) `captured_at` is
  **never modified** — a same-instant same-key collision (near-impossible with a
  truthful microsecond `now`) is disambiguated by a **generation suffix** `__g0001`
  in the *filename only*, and a holder's bare base sorts ahead of its `__gNNNN`
  contenders. Four
  revisions are retracted along the way, each of which fabricated event time to buy
  filename uniqueness: `-2`/`-3` suffixes (also broke sort), advancing only the
  filename (path/frontmatter disagreed), advancing a whole second, and advancing a
  microsecond (still rolls past a second boundary — e.g. `…59.999999` → next
  second — and inverts a genuinely-later capture). Filenames also carry microsecond
  precision so the common uncontended case is unique and round-trips. This is a
  spec §1 filename-format change (base grammar plus the optional `__g` generation
  token); readers tolerate it because ordering is by `captured_at`, not the name —
  no `STORE_SCHEMA_VERSION` bump.
- **Detached freshness can silently skip.** By design — "curate ran" is no longer
  guaranteed by a `SessionStart`; the next non-contended launch freshens.
- **The lock is advisory/cooperative.** A writer that bypasses `project_lock`
  bypasses serialization. Same shape as G1's schema guard, and an argument for
  eventually converging both on a single `open_store(root, project)` handle. Not
  required by this ADR.
- **`Document` grows a `digest` field** (`None` for documents not read from disk).
  Cheap — one SHA-256 per `read_doc` — and it backs `mark_consumed`'s defensive
  change-detection guard. It is **not** a compare-and-swap and must not be relied
  on as one: its check and its write are separate syscalls (P1-DATA-INTEGRITY-002).
- **Verification status.** `core/lock.py`, the contended-scribe path, the curator
  lock wiring, and three probe suites (`tests/test_store_lock.py`,
  `tests/test_raw_consume_cas.py`, `tests/test_lock_wiring.py`) exist ahead of
  acceptance. The full mandatory gate — `make ci`: ruff check, ruff format, mypy,
  store-chokepoint, pytest — is **green at 1391 tests** on this branch. (An earlier
  revision claimed a green "full gate" on `pytest` alone while `make ci` was red;
  that claim was wrong and is retracted.) The probes cover cross-process mutual
  exclusion (8×40 == 320 exact, with an unlocked control that corrupts),
  non-blocking skip, blocking timeout, release on exception, **SIGKILL'd holder
  auto-release**, empty/garbage lockfile recovery, successor-lock safety, canonical
  naming and oldest-first order for contended captures, the **holder-vs-loser
  same-instant handoff** (P1-DATA-INTEGRITY-001 — a contended capture never claims
  the base a holder is about to write, in either interleaving), the wiring at each
  real entry point (both scribes, seed import, MCP `memory_remember`, and the
  CLI/engine `--if-stale` skip), and the raw handoff driven through the **real**
  `engine.curate` — the latter verified by mutation: neutralizing the curator lock
  makes both end-to-end tests fail. **The Windows leg is exercised only in CI**, not on this
  machine.

## Alternatives considered

- **Hand-rolled `O_EXCL` lockfile with tokens, heartbeats, and stale takeover.**
  The first draft of this ADR. **Rejected after review** — see the Decision: a
  read-token-then-mutate-path sequence does not provide mutual exclusion, and the
  recovery story it depended on was demonstrably false. Recorded here rather than
  deleted, because the appeal (no dependency, one code path) is exactly what will
  tempt the next author.
- **`fcntl.flock` + `msvcrt.locking` adapters written in-repo.** The same
  handle-bound semantics with no dependency, at the price of two platform code
  paths — including a Windows path that cannot be exercised on the author's
  machine. Rejected in favour of a vetted library for precisely that untestable
  leg; `filelock` is a thin wrapper over these same primitives.
- **Per-store (whole-`root`) lock.** Simpler — one lock for everything. Rejected:
  it serializes unrelated projects, so a `SessionStart` in one project would block
  a curate in another, needlessly widening contention on the hook's hot path.
- **Blocking the scribes on the lock, for a uniform boundary.** Rejected: hooks
  must exit 0 inside a latency budget (ADR-0003); blocking one behind a curate pass
  would blow it, and failing it would drop the capture. A *non-blocking* acquire
  with a fresh-filename fallback gets the safety at no latency cost, which is what
  the Decision adopts.
- **Guarding the raw race with a digest check alone, leaving scribes lock-free.**
  Tried in round 2 of this ADR and **rejected after review**: check-then-write in
  two syscalls is not atomic, and the original lost-capture outcome remained
  reachable. Naming a residual window does not bound its consequence.
- **Fix `prune_tombstones` in isolation with an atomic claim** (rename-to-claim,
  verify, then delete-or-restore). Rejected as the *primary* fix: it patches one
  symptom while duplicated fold work and interleaved supersession remain. Once the
  per-project lock exists, prune's race is closed for free.
- **Serialize by making the curator a singleton daemon / queue.** Heavier
  architecture, a resident process, and it contradicts the detached best-effort
  model (D8). A file lock keeps every pass a short-lived, crash-safe process.
