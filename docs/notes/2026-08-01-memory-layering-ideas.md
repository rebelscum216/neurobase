# 2026-08-01 — session summarization, memory layering, and what to borrow from the vault

**What this is.** A design conversation between Andrew and Claude on the night of
2026-07-31 → 2026-08-01, starting from "why aren't today's sessions showing in the
UI" and ending at a proposed layering for how sessions become memory. **Nothing here
is a contract** — notes carry no authority (see [README](README.md)). It is written
up for Andrew to review later, and the decisions at the end are the ones he still
needs to make.

Andrew's own framing is preserved in §4 — the core proposal is his, and §5 is
Claude's attempt to sharpen it into something buildable.

**Project slugs are pseudonyms.** `project-a`, `project-b` and `project-c` stand in for
three of Andrew's internal work repos, consistently throughout — the arms of every
experiment below (`project-a` = the authored-wrap arm, `project-b` = the control) keep
their identity, only their names are neutralized for publication.

**Code-review update (2026-08-01).** §8 records a pass over the actual Neurobase
implementation at `feat/webui-phase-g-graph` commit `8e61e27`, plus the vault's
current packet templates and health checker. It leaves the original conversation
visible but corrects three conclusions: the proposed four "types" are really three
separate axes, `pinned` should not be retired, and the vault checker validates
structure rather than semantic truth.

---

## 1. How we got here

Booting the web UI surfaced two unrelated surprises, both worth recording because
both were misread as bugs before being understood.

**There are two stores on this machine.** `~/neurobase-dev` (created 2026-07-15,
project `project-c`, 90 captures, newest 2026-07-15) and `~/neurobase` (created
2026-07-31 19:24 UTC, projects `neurobase` + `project-a`). Nothing was lost and
nothing failed to consolidate — they were never the same store. `resolve_root()`
precedence is explicit arg > `NEUROBASE_ROOT` > config > default `~/neurobase`, and
with no `~/.config/neurobase/config.toml` on this machine, anything not passing
`--root` lands in the default. The July work only ever reached the dev store because
it was pointed there by hand.

**The live store is hours old, not months.** `~/.claude/settings.json` was written
2026-07-31 15:24 local — the same minute `~/neurobase` was born — and the store's own
`backups/` holds the pre-install `settings.json` and `config.toml`. Hooks were
installed for the first time that day. The July captures in the dev store were a
backfilled pass, not accumulated live capture.

Neither is a defect. Both are recorded because "the memory lost my history" was the
first reading in both cases, and it was wrong in both cases.

---

## 2. What we established about the current design

All verified against source on 2026-07-31 unless marked otherwise.

### 2.1 Consolidation exists and is good

`curate` is a real fold. The plan step sends the brain the project's **current
curated facts alongside the new raw captures**, with a deletion-first mandate
(`engine.py`: *"optimize for deletion and merging, not accumulation"*), rules to
reuse slugs over writing near-duplicates, `supersedes` driving an automatic
tombstone in the same pass, then prune and node re-synthesis. Merging, superseding
and deletion are all first-class.

### 2.2 But currency is coupled to activity — filed as G8

Written up during this session as **G8** in `docs/known-gaps.md` (*was* uncommitted in
the `nb-phase-g` worktree; **committed 2026-08-02 as `c901cd3` on `main`** — see §9.1).
Summary: three shipped surfaces
claim a "current" fact set unconditionally, and `README.md:73` makes it the
differentiator in a comparison table, but every mechanism that could correct a fact
is reached only through the arrival of new raw captures:

- `if not raw_docs:` returns `{"status": "noop"}` **before any brain call**.
- `--if-stale` measures *unconsumed raw age*, never fact age. Nothing ages, expires,
  or decays an active fact.
- `pinned` facts are permanently exempt from correction.
- The prompt says "include only facts that change; omit unchanged ones."
- Nothing grounds a fact against the repo — only a later conversation can contradict
  it.

### 2.3 No session is durably summarized

This is the finding that drove the rest of the conversation. Three artifacts exist
per session, and none of them is a durable summary:

| Artifact | When | LLM? | What it actually is |
|---|---|---|---|
| Raw capture (`raw/*.md`) | session end | **no** | Deterministic skim. Its `## Final assistant summary` is *selected, not generated* — `final_summary()` returns the **longest of the last 3 assistant messages** |
| Distill digest (`raw/.digests/*.md`) | during `curate` | yes | The only real per-session summary — but a **cache**, keyed by a fingerprint of raw body + transcript path/size/mtime + redact patterns + cache version |
| Facts / nodes | during `curate` | yes | Cross-session and per-project respectively — **not** per-session |

Measured on this machine at the time of writing: **7 of 102 sessions** have ever been
summarized by an LLM (`project-a` 7 of 8; `neurobase` 0 of 4; `project-c` 0 of
90) — and those 7 as a disposable cache, not a record.

**G10 — FILED AND MERGED 2026-08-02** (`16fd32c`, shipped in PR #14 as `3642bd9`).
⚠ *Renumbered — this was written as "G9", but G9 was taken by the
`seed --from-claude-memory` encoding defect before this candidate was filed; that
defect has since been **fixed** (PR #14). See §9.1 and §10.* Because the digest
requires the transcript to still
be on disk, and `curate` sits behind a 12h staleness gate, a session whose transcript
rotates or is deleted before curation can *never* be distilled. Distill falls back
silently; no error. Plausibly what happened to all 90 dev-store sessions. This is a
distinct mechanism from G8 — G8 is "facts stop being re-examined once a project goes
quiet," this is "a session's richest available record can expire before anything
reads it."

### 2.4 The UI has nothing better to show

`_session_card.html` renders `<pre>{{ sel.body }}</pre>` — the raw skim, verbatim.
The readability problem is not a UI defect; there is no better artifact to display.

---

## 3. What the vault does differently

The useful framing: `~/vault` is a **working prototype of what Neurobase automates**
— the same job, in daily use across 19 projects, but hand-maintained. Where the two
diverge, the vault has already stress-tested the decision.

Worth stating plainly: at the time of writing, the vault's `projects/neurobase/`
packet held **824 lines of accurate current state** — including the G8 entry written
an hour earlier — while Neurobase's own store for the same project held 4 raw
captures and zero facts. On the project that builds the automatic memory system, the
manual one is winning.

| Pattern | Vault | Neurobase |
|---|---|---|
| Typed documents with per-type lifecycle | README / WORKLOG / AGENTS / TODO / reviews / sessions | one flat fact type |
| Runnable structural checker over packet topology and metadata | `check_vault.py`, 156 lines stdlib | `doctor` checks the install, never the facts |
| Scheduled passes, **graduated** write authority | 3 daily scans, tiered | no scheduler; binary consent |
| Corrections stay visible, dated | 189 explicit supersede/correction markers | tombstoned, hidden, hard-deleted at 14d |
| Git-backed history | yes — a local commit *is* the propagation | no history |
| "Start here" shortlist | refreshed at every wrap | node states what's true, not what to do |
| Explicit source-of-truth ordering | numbered 1–5 in packet `AGENTS.md` | nothing adjudicates conflicting facts |

**What not to borrow.** The vault is expensive: it needs a health-check skill
*because it drifts*, its `last_edited` frontmatter goes stale, and its two hooks cost
**61–65 ms of every tool call in every repo** (measured, accepted, irreducible
without merging interpreters). Neurobase's deterministic, no-LLM-in-the-hook posture
is the better call and should not be traded away. Borrow the shapes, not the
mechanism.

---

## 4. Andrew's proposal (2026-08-01)

In his words, condensed:

> The wrap skill has the agent summarize everything while it's fresh and it's its own
> work. Feels like I should do that for NB, or at least have the option to. And then
> if I forget to wrap or don't want to, then have that conversation tagged — or tag
> it if it *is* wrapped. If it's not wrapped, we use the existing Neurobase process
> with the hooks to auto-get the transcript and curate and pull stuff out of it. I
> also want a conversation summarized so that when I'm looking at it in the UI it's
> easier to read. Finally I like the part about the vault having different data types
> and layers.

---

## 5. The design that falls out

### 5.1 The wrap summary is the top rung of a digest ladder

Self-summarization beats distill on **information the transcript does not contain**:
what was tried and abandoned, why a path was rejected, what is uncertain, what to do
next. A transcript records only what was said.

The cost side favours it too. Per the 2026-07-31 field report, `tool_use` +
`tool_result` are **68%** of a session file and user-typed text is **1.6%**; distill
spends ~3 brain calls / 65 s per capture compressing mostly mechanical noise.

**A planner insertion point already exists, but the durable storage point does not.**
`distill_docs()` returns *body-substituted Document copies* — digest as body,
`file_path` and frontmatter preserved so `provenance` and `mark_consumed` still
target the real raw file. That makes it the right place to select the best available
input for curation. It does **not** make `raw/.digests/` the right home for an
authored wrap summary: digests are disposable, content-addressed cache entries. A
wrap is durable history and needs a first-class session record outside that cache.

| Rung | Source | Carries |
|---|---|---|
| 1 | agent-authored wrap summary | intent, decisions, dead ends |
| 2 | LLM distill of transcript | content |
| 3 | deterministic skim | always available, no LLM |

This reframes the tagging question: **the tag is not a label, it is which rung the
session got.** It is also a cost win — a wrapped session skips distill entirely.

**Wrap must stay optional.** The moment memory *depends* on a ritual, it develops a
hole precisely where the user was busiest and least likely to perform it. The ladder
degrades on its own; that is the point.

**Two hazards to design against:**

- **Sequencing.** Wrap runs *during* a session; the scribe writes the capture at
  session *end*. The wrap command cannot write into a capture that does not exist
  yet, and if it writes its own, one session yields two captures. Store the summary
  under a canonical session identity and link it to captures by `session_id` —
  already present in raw frontmatter alongside `transcript_path` and
  `capture_version`. Do not assume the relationship is one-to-one: a long session
  can produce multiple captures if curation consumes one before the session ends.
- **Self-assessment.** A wrap summary is the agent grading its own homework: the same
  agent that just concluded its work was correct. So it must **never suppress the
  deterministic capture** (the raw stays as the independent check), and the session
  record should carry explicit author/method/authority metadata so self-report and
  transcript-derived summaries remain distinguishable downstream.

### 5.2 A session summary cannot be a "fact"

The fold is instructed to optimize for deletion and merging. **You cannot merge two
sessions** — they are events, not claims. A session summary is *history*, and history
is append-only.

So §2.4 (unreadable UI) and the typed-layers idea are **the same problem**: the flat
model has no shape meaning "an immutable record of a thing that happened."

### 5.3 Three independent axes, not four types

The first version mixed what a record *is*, how it *changes*, and who has authority
to change it. Those need to be independently queryable.

| Axis | Examples | Controls |
|---|---|---|
| `kind` | session, fact, project-status, worklog-entry, task, rule, decision, review, person, goal, result | display, relationships, and source-specific behavior |
| `lifecycle` | replaceable, append-only, closeable, decision-controlled, derived/ephemeral | merge, supersede, close, retain, and delete policy |
| `authority` | human-authored, user-directed, agent-authored wrap, curator-derived, deterministic capture, external canonical | who may propose, accept, correct, or overwrite |

A session summary is therefore roughly `kind=session`, `lifecycle=append-only`,
`authority=agent-authored-wrap`. An open task is `kind=task`,
`lifecycle=closeable`; it may be human-authored or curator-derived. A project status
record is `kind=project-status`, `lifecycle=replaceable`, but its authority depends
on its source.

Verified: there is no `kind`, `lifecycle`, or generic record abstraction in the
current `core/store.py`. Curated facts carry `name`, `status`, `supersedes`,
`provenance`, `agent_last`, and `updated_at` (plus the seed importer's
`source_path` / `source_digest`), and `status` is only `active|tombstoned`.

**Do not retire `pinned` yet.** It is crude, but it expresses consent/authority, not
a content type. A user-directed fact might be a status, rule, task, or decision.
Keep that protection until there is an explicit contradiction and correction
workflow: the system may flag a protected record and propose a replacement, but it
must not silently overwrite it. The eventual model can replace the boolean with
clearer authority and policy fields without weakening the guarantee.

### 5.4 Structural checks and semantic verification are different jobs

The vault's `check_vault.py` is 156 lines of stdlib with no dependencies, but it
checks the Active Project Map, packet completeness, tracked/untracked state, output
growth, `last_edited`, and time-label conventions. It does **not** decide whether a
status is still true, a TODO is complete, two documents contradict each other, or a
review is resolved.

Neurobase should have a committed, runnable checker for mechanical invariants: an
append-only record was never rewritten, every source reference resolves, a protected
record was not silently dropped, and catalog/index state can be rebuilt from
authoritative files. Semantic currency is a separate, source-grounded audit. A timer
that asks the model to reconsider its own previous facts is not verification; the
audit needs repo state, vault packets, explicit user corrections, or type-specific
verifiers as evidence.

---

## 6. Open decisions for Andrew

1. **Smallest first step?** Rungs **1 and 3 only** remain a good product experiment:
   accept an authored wrap summary, fall back to the existing skim, and have the
   session UI show which rung it is displaying. The correction from code review is
   that the summary should be a durable session record, not another digest-cache
   entry, even if the broader type system waits.
2. **How does a wrap summary reach the store?** A CLI command the wrap skill calls
   (`neurobase wrap --summary -`?) writing a durable session record seems cleanest
   and needs no hook changes. The record should use a canonical session identity and
   link all matching raw captures rather than assume one sidecar per capture.
3. ✅ **ANSWERED 2026-08-06 (Andrew): YES — rewrite it now.** Drafted as
   **ADR-0028** (`Accepted` 2026-08-06, `f736df4`), which superseded 0016, carries **D27–D31
   forward with their numbers intact** (ADR-0017 cites D27, ADR-0018 cites D29 — a
   renumber would strand both), and adds the record model as **D49–D54**.
   ⚠️ **The stated basis matters, because half the original argument is dead.** §8.4
   gave two reasons to bundle; the *migration-economy* one is **falsified** — measured
   on this commit, `authority` has three write sites and **zero read sites**, and D46
   already fixes that schema-1→2 carries it forward unchanged, so there is no migration
   to save. What survives is §8.4's *other* argument, the shared abstraction: a
   per-document key is cheap **only while nothing reads it**, and the reader-side sweep
   does not get cheaper by waiting. ADR-0028 rests on that one alone and says so.
   ⭐ D50 is the guard against repeating ADR-0027's outcome — a correct field nothing
   consumes — by making "at least one surface reads through the abstraction" an
   acceptance criterion of the migration itself. **D54 is a knock-on from decision 7:**
   the §5.3 `kind` list (`person`, `goal`, …) was drafted while mounting the vault was
   live, so those kinds are deliberately not adopted.
   *Original question:* ~~**Revise ADR-0016 before implementing schema 2?** The accepted-but-unimplemented
   schema already introduces project records, profiles, ULID event IDs, and content
   hashes. Adding record/source identity and the three axes there avoids landing
   schema 2 and immediately needing schema 3.~~ *(The "immediately needing schema 3"
   worry is the falsified half — nothing needed migrating.)*
4. ~~**File G9** (transcript expiry, §2.3) as its own gap, or fold it into G8?~~
   ✅ **DONE 2026-08-02** — filed as **G10** (the G9 slot was already taken) and
   merged in PR #14. It is its own gap, not folded into G8.
5. ~~**Scheduled audit pass** (the vault's tiered-authority model, the direct fix for
   G8) — in scope for this workstream, or separate?~~
   ✅ **ANSWERED 2026-08-06 (Andrew): SEPARATE — and G8's claim half fixed the same
   day.** The audit pass (`curate --audit`) is its own workstream; this note is about
   session summaries, and the two share a motivation but no mechanism. G8's own **Fix
   direction** already split this way independently, marking the claim half "required
   either way" — so that half was shipped immediately (four sites: `README.md` opening
   paragraph, the "A curator that deletes" bullet, the comparison-table `Fact set` row,
   `docs/how-it-works.md` step 2). **G8 stays `open`** for the unbuilt mechanism rather
   than being re-filed as a second gap, since its entry already carries both halves.
   ⛔ Narrowing the claim does **not** close G8, and could not: pinned facts are exempt
   from correction by design, so no audit pass can restore the unconditional reading.

## 7. Related artifacts

- `docs/known-gaps.md` — **G8**, written during this session. *(Was uncommitted in the
  `nb-phase-g` worktree; adopted as `c901cd3` and shipped in PR #13. Still `status:
  open` — it is a filed gap, not a fixed one.)*
- `docs/notes/2026-07-31-first-enable-field-report.md` — the other session's
  first-enable measurements, source of the 68% / 1.6% / 12.4% figures.
- `~/vault/.codex/skills/vault-health-check/scripts/check_vault.py` — the 156-line
  checker referenced in §5.4.
- `~/vault/projects/neurobase/` — the packet whose accuracy prompted §3.

**Status:** none of this is decided and no implementation was changed. This note was
updated with the code-review analysis in §8.

---

## 8. Codex code-review recommendation (2026-08-01)

**Review identity:** this note reviewed against Neurobase
`feat/webui-phase-g-graph@8e61e27`, pass 1. **Disposition: COMMENT** — the revised
note is suitable decision input, but there is no implementation artifact to approve.

| Finding | Severity | Resolution in this note |
|---|---|---|
| `MEM-001` — the proposed integration seam was above several direct store readers | blocking design | §8.2 moves the seam below search, graph, UI, MCP, and recommendations |
| `MEM-002` — four proposed types conflated kind, lifecycle, and authority | blocking design | §5.3 separates the three axes |
| `MEM-003` — retiring `pinned` would weaken user-directed authority before a replacement exists | blocking design | §5.3 preserves the guarantee and proposes contradiction/replacement workflow |
| `MEM-004` — treating a wrap summary as a digest-cache entry made durable history disposable | blocking design | §§5.1 and 8.3 make it a first-class session record |
| `MEM-005` — the vault checker was described as semantic when it is structural | non-blocking accuracy | §§3 and 5.4 narrow the claim and separate verification |

### 8.1 Verdict: build the product on Neurobase; mount the vault as a source

If the choice is "start with Vault and import Neurobase" versus "start with
Neurobase and add the best of Vault," start with **Neurobase**.

Neurobase already owns the hard application concerns: deterministic capture,
redaction, consent-gated mutation, a controlled Markdown store, curation,
tombstoning, derived nodes, search, graph construction, MCP tools, CLI flows, and a
web UI. The vault owns a better *information model* for real work: typed packets,
explicit source-of-truth ordering, append-only worklogs, goals/results, review state,
and visible corrections. Rebuilding the former inside the vault would create a
second application runtime. Flattening the latter into Neurobase facts would destroy
the structure that makes it useful.

The target is therefore:

> **Neurobase is the memory engine and interaction layer. The vault is one
> authoritative workspace provider, read in place.**

Borrow the vault's documented shapes and behavior, not its retired memory scripts
or implementation. Its Markdown packets are evidence and product requirements; they
are not source code for Neurobase.

### 8.2 Why this is not just a `VaultProvider` bolted onto the UI

The actual code has no provider-neutral read model today:

- `core/search.py` enumerates only curated facts and status nodes, and
  `SearchHit.kind` is literally `curated|node`.
- `core/graph.py` models sessions, facts, session-to-fact provenance, and fact
  supersession. The Phase G view adds proposals, but still composes the same concrete
  corpus.
- `webui/routes.py` reads `list_raw()`, `list_curated()`, and the `nodes/` directory
  directly. The session card renders the raw body as a transcript.
- `mcp/server.py` exposes fact/node/project operations directly; `memory_remember`
  writes a curated fact.
- `recommender/corpus.py` explicitly loads active curated facts, capped raw captures,
  and the decision ledger.

A vault-only branch in one route would leave search, graph, MCP, and recommendations
with different memories. The shared seam must sit below all of them.

Introduce a provider-neutral read/catalog layer in `core`, conceptually:

```text
MemoryRecord
  id / source / source_ref / project
  kind / lifecycle / authority
  title / body
  observed_at / valid_at / updated_at
  provenance / relations / verification

MemoryProvider
  enumerate(project, filters) -> MemoryRecord[]
  read(record_id) -> MemoryRecord
  capabilities() -> read/write/propose/verify
```

Start with `NeurobaseStoreProvider` and `VaultProvider`. Keep Markdown authoritative;
use a disposable SQLite/FTS catalog for unified lookup, filtering, and joins. The
catalog must be fully rebuildable and must never become the only copy of a memory.

Writes stay source-specific. Neurobase may mutate its controlled store through its
existing consent boundary. Vault changes should use typed commands that preview a
diff and preserve its local Git history; the curator must never rewrite an arbitrary
packet as if it were a replaceable fact.

### 8.3 The first-class session record

The current capture/digest code provides a clean selection ladder but not the right
durability:

1. authored wrap summary, when present;
2. transcript distill, while the transcript is available;
3. deterministic skim, always.

Store the selected/authored summary as a session/history record with its method,
authoring agent, capture links, and revision/finalization state. Raw captures remain
the independent evidence and are never suppressed. Curation can consume the best
available representation; the UI can display the summary first and expose the raw
capture underneath.

This also makes the transcript-expiry gap separate from G8 (⚠ *called "G9" when
written; the G9 slot is now taken — renumber to **G10**, §9.1*):

- **G8:** established facts are not re-evaluated when a project becomes quiet.
- **G10:** the richest source for a session summary can disappear before first
  distillation.

They need different fixes. G9 needs prompt capture/summary durability. G8 needs
source-grounded verification and correction policy.

### 8.4 The schema-2 decision is the leverage point

`ADR-0016` has already accepted a schema-2 migration with project records, profiles,
ULID event IDs, content hashes, and monorepo subpaths, but the migration is not yet
implemented. That is the moment to add source identity and a typed record/catalog
model. Shipping the existing schema-2 design first and adding layering afterward
would likely force another store migration and touch the same readers twice.

Do not add `kind` only as extra frontmatter on today's curated facts and then teach
every surface to branch on it. `upsert_curated()` technically accepts extra
frontmatter, but search, graph, UI, MCP, and recommendation still enumerate concrete
directories and concrete object types. That shortcut changes storage without
creating the shared abstraction the product needs.

### 8.5 Recommended sequence

1. **Architecture slice:** revise/supersede ADR-0016 with the provider-neutral record
   model, the three axes, source-specific mutation, and the rebuildable catalog.
2. **Session slice:** add a durable authored session summary plus the
   summary → distill → skim selection ladder; update the session UI to show the rung
   and retain access to raw evidence.
3. **Read slice:** implement `NeurobaseStoreProvider`, `VaultProvider`, and catalog;
   move search onto it first because search is the narrowest complete consumer.
4. **Surface slice:** move graph, web UI, MCP reads, and recommender corpus onto the
   same catalog. Preserve their source-specific write commands.
5. **Trust slice:** add mechanical invariant checks, type-specific verifiers,
   contradiction proposals, and explicit acceptance for protected/user-directed
   records.

The smallest useful experiment is still the session slice, but write its durable
record behind the intended provider boundary so it does not become another
schema-1-only artifact.

### 8.6 Final answer to the original framing

Use **Neurobase as the foundation** and make the vault its first rich external memory
source. Import the vault's lifecycle semantics, authority model, packet relationships,
source-of-truth ordering, worklog/history behavior, and review/open-loop concepts.
Do not turn the vault into the app, and do not flatten the vault into Neurobase's
current fact directory. The optimal app is Neurobase with a general memory record
layer that can faithfully represent both.

---

## 9. Claude's review of §8 (2026-08-02)

Verified against `main@7201c60` — the branch this note was written on no longer
exists. §9.1 is factual staleness that must be fixed regardless of any design
decision; §9.2 accepts three of §8's findings and sharpens one; §9.3 is where I
disagree or think something material is missing.

### 9.1 The note went stale in four ways while it sat

1. **`G9` is taken — the transcript-expiry candidate is now `G10`.** It was filed
   2026-08-02 as `7201c60` for a different defect (`seed --from-claude-memory`
   mis-derives Claude Code's directory name and exits 0 — the mailbox field report).
   §2.3 and §8.3 both claimed the G9 slot; both are corrected in place above. This is
   the one error that would have caused real damage: two sections pointing a reader at
   the wrong gap.
2. **G8 is committed.** `c901cd3` on `main`, no longer an uncommitted worktree edit.
3. **The review identity is a dangling SHA.** PR #11 was **squash-merged** as
   `b287055` (single parent `a9fdf43`), so `feat/webui-phase-g-graph@8e61e27` exists
   as an object but is **not an ancestor of `main`** — `git log` on `main` will never
   show it. §8's code observations still hold (I re-verified `SearchHit.kind` is
   `"curated"|"node"` on `main`), so the substance survives; only the identity is
   unresolvable. Worth noting this is exactly the hazard the vault's own `AGENTS.md`
   documents about handing reviewers a SHA that later stops being reachable.
4. **The `nb-phase-g` worktree is gone**, with a `nb-g8-known-gaps.patch` left beside
   the repo. Any instruction in this note that references that path is dead.

### 9.2 Where §8 is right

- **`MEM-004` is the best finding in the pass, and the real mechanism is worse than
  stated.** §8 calls the digest cache "disposable." It is *self-erasing*: entries are
  keyed by a fingerprint of raw body + transcript **path, size, and mtime** + redact
  patterns + cache version. An authored wrap summary stored there would be silently
  destroyed the next time the transcript is touched — not merely evicted at some
  point, but invalidated by a routine event with no error and no trace. That closes
  the question; the wrap summary cannot live in `raw/.digests/`.
- **`MEM-003` is right about the axis, and does not weaken the original objection.**
  `pinned` encodes authority, not kind — my "rule" type conflated the two, and
  deleting the protection before a replacement exists would be strictly worse. But the
  problem that motivated it is unchanged: **a pinned fact still cannot be corrected at
  all.** §5.3's "flag a protected record and propose a replacement, never silently
  overwrite" is the actual resolution, and it should be written as a *requirement* of
  the eventual authority model rather than an aspiration, or G8's third bullet
  survives the entire redesign.
- **`MEM-005` is a fair correction.** I wrote "integrity checker over memory content";
  `check_vault.py` checks packet topology and metadata. The claim is narrowed above.

### 9.3 Where I disagree, or think something is missing

**a. The multiple-captures claim in §5.1 is inverted.** §5.1 now warns that "a long
session can produce multiple captures if curation consumes one before the session
ends." I found no support for this and direct evidence against it: across all 17 raw
captures in `~/neurobase`, **every `session_id` appears exactly once**, and
`adapters/codex/scribe.py:4` states Codex writes "on every turn and **overwrite one
raw file per session in place**." The real hazard is the opposite shape — one capture
per session, **repeatedly overwritten mid-session**. That strengthens `MEM-004`'s
conclusion while correcting its reason: anything authored during a session must live
outside the raw document, or a later turn will clobber it. The sentence should be
rewritten rather than kept.

**b. The sequence contradicts its own "smallest experiment."** §6.1 and §8.5 both name
the session slice as the smallest useful experiment, but §8.5 orders the architecture
slice *first* and directs the session record to be written "behind the intended
provider boundary." That makes the cheap experiment depend on an ADR revision, a
provider model, and a catalog. It cannot be both the smallest step and gated on the
largest one. My view: keep the experiment genuinely small and accept it may need
migrating. A schema-1-only session record that answers *"do authored summaries
actually read better than distilled ones"* is worth more than a correct abstraction
built before anyone knows the answer — and if the answer is no, the entire provider
model is being justified by a premise that failed.

**c. `VaultProvider` is a strategic expansion that was not asked for, and it has an
unraised data-boundary problem.** The question on the table was what to *borrow* from
the vault. §8 proposes to *mount* it as a live read source. Those are very different
commitments. The vault holds personal notes, internal admin requests, and client work;
Neurobase is an open-source project with a held PyPI publication. Mounting the vault
would route that content into Neurobase's search, graph, and — most consequentially —
**MCP** surfaces, which exist to expose memory to any connected client. Nothing in §8
distinguishes "a personal adapter Andrew runs locally" from "a provider that ships."
That distinction should be settled before the provider model is designed around it,
because it changes what the abstraction has to guarantee.

**d. The wrap-destination collision is missing entirely.** Andrew already has `/wrap`,
which writes a durable session summary — **to the vault packet**. If `neurobase wrap`
also writes one, there are two wrap destinations for the same work, maintained by the
same agent, at the same moment. They will drift, and §1 of this very note is a case
study in two memory stores that were assumed to be one. Under §8's mount-the-vault
proposal it gets sharper still: Neurobase would be *reading* the vault packet while
*writing* its own summaries about the same sessions, with no stated precedence. This
is the most practical unanswered question in the note and it belongs in §6 as a
decision: does `neurobase wrap` replace the vault worklog entry, duplicate it, or
derive from it?

### 9.4 Suggested additions to §6

6. **Ship the session slice schema-1-only, or gate it on the architecture slice?**
   (§9.3b — they are presented as compatible and are not.)
7. ~~**Is `VaultProvider` a shipped feature or a personal adapter?**~~ (§9.3c — decide
   before designing the provider boundary, since MCP exposure is the crux.)
   ✅ **ANSWERED 2026-08-06 (Andrew): NEITHER — no provider at all. Borrow the
   shapes only.** Stronger than §10.2's "personal adapter, out of scope": the
   question is closed rather than parked. Everything §3's table actually credits
   the vault with — typed records with per-type lifecycle, append-only history,
   visible dated corrections, explicit source-of-truth ordering, a "start here"
   shortlist — is a *shape*, borrowable with nothing mounted. Mounting was never
   what was asked for (§4 asked what to **borrow**; §8 answered what to **mount**),
   and it would route personal notes, admin requests and client work into the
   MCP surface. Forecloses nothing: an adapter stays additive if it is ever wanted.
   ⛔ **This unblocks decision 3**, which §10.2 had gated on 7.
8. **What is the relationship between `/wrap` → vault and `neurobase wrap` → store?**
   (§9.3d — replace, duplicate, or derive.)
9. ~~**File `G10`** (transcript expiry)~~ — **done 2026-08-02**, filed as `G10` in
   `docs/known-gaps.md`. The original decision #4 was pointing at an occupied slot
   (§9.1).

---

## 10. Decision brief (2026-08-03)

Re-verified against `main@3642bd9`. §§1–9 are left intact; this section states what
changed underneath them and gives a recommendation on every open decision, so §6 can
be answered without re-reading the whole note.

### 10.1 What changed since §9 — and what did not

| §9 said | Now |
|---|---|
| Verified against `main@7201c60` | `main@3642bd9`; two PRs (#13, #14) landed in between |
| G9 (`seed --from-claude-memory`) is a filed gap | **FIXED** in PR #14 — and the fix took three attempts, two of which were wrong |
| G10 is a candidate, not yet filed | **Filed and merged** (`16fd32c` → `3642bd9`) |
| G8 uncommitted in a worktree | Shipped in PR #13 — **still `status: open`** |
| `nb-phase-g` worktree gone, patch left beside the repo | Patch now redundant; G8 landed |

**Four load-bearing claims re-verified on `main@3642bd9`**, because a design brief
that rests on stale code reading is worse than none:

- `SearchHit.kind` is still literally `"curated"|"node"` (`core/search.py:31`) — §8.2's
  "there is no provider-neutral read model" argument **stands unchanged**.
- The digest fingerprint still includes the transcript's `st_size` and `st_mtime_ns`
  (`curator/distill.py:277-278`) — the **self-erasing** property in §9.2 is real, and
  it remains the single strongest finding in this note.
- **19 raw captures, 19 distinct `session_id`s, no duplicates.** §9.3a's correction to
  §5.1 holds on a larger sample than when it was written (was 17). The hazard is
  overwrite-in-place, not multiple captures per session.
- `check_vault.py` still exists at the path §5.4 cites.

**The G9 fix is direct evidence for one of the open decisions.** It took three
attempts: `/` only, then `/ . space` (documented as "observed, not specified" and
*still wrong* — it missed every underscore path), then `[^A-Za-z0-9-]`, settled by
running one deliberate experiment. That is a live demonstration of §9.3b's argument:
the cheap experiment that produces real evidence beat two rounds of careful reasoning
from insufficient samples. It is the same shape as "does an authored summary read
better than a distilled one" — a question no amount of architecture will answer.

### 10.2 Recommendations on the open decisions

Decisions 1–5 are §6's; 6–8 are §9.4's additions. Ordered by what unblocks what.

| # | Decision | Recommendation |
|---|---|---|
| **7** | Is `VaultProvider` shipped or personal? | **Personal adapter, out of scope for now.** Decide first — it gates 3 and 6. |
| **8** | `/wrap` → vault vs `neurobase wrap` → store | **Derive, don't duplicate.** Decide first — it defines what the session slice writes. |
| **6** | Session slice schema-1-only, or gated on architecture? | **Schema-1-only.** |
| **1** | Smallest first step | Rungs 1+3, durable session record, UI shows the rung. Unchanged and still right. |
| **2** | How the summary reaches the store | CLI command, keyed by `session_id`. Now better supported: 19/19 uniqueness. |
| **3** | Revise ADR-0016 before schema 2? | **Not yet** — blocked on 7. |
| **4** | File G9/G10 | ✅ Done. |
| **5** | Scheduled audit pass | **Separate workstream.** It is G8's fix, not this note's. |

**Why 7 and 8 come first.** Both are scope questions that change what gets built;
1–3 are implementation questions that change how. Answering an implementation
question before a scope question is how §8 ended up ordering the architecture slice
ahead of the experiment it was meant to support.

**7 — `VaultProvider` should be a personal adapter, not a shipped feature.** The
question Andrew asked was what to *borrow*. §8 answered what to *mount*, which is a
different and much larger commitment. The decisive argument is the data boundary
§9.3c raised: the vault holds personal notes, admin requests, and client work, and
Neurobase's **MCP** surface exists to expose memory to any connected client. Shipping
a provider that can mount arbitrary local directories into that surface is a security
design problem, not a convenience feature — and Neurobase has a held PyPI
publication, so "it only runs on Andrew's machine" stops being true the moment it
ships. Borrowing the vault's *shapes* (typed records, append-only history, visible
corrections, source-of-truth ordering) needs no provider at all.

**8 — `neurobase wrap` should derive from the vault entry, or the two should not
both exist.** `/wrap` already writes a durable session summary to the packet, at the
same moment, by the same agent. Two destinations for one piece of work is exactly the
failure §1 of this note documents — two stores assumed to be one. The cheapest honest
option: `neurobase wrap --summary -` accepts text on stdin and `/wrap` pipes it the
same summary it just wrote. One authored artifact, two destinations, no second act of
authorship and no drift. Anything else needs a stated precedence rule, and none has
been proposed.

**6 — ship the session slice schema-1-only.** §6.1 and §8.5 both call it "the
smallest useful experiment" while §8.5 gates it on an ADR revision, a provider model
and a catalog. It cannot be both. The experiment answers *"do authored summaries read
better than distilled ones"*; if the answer is no, the provider model was justified by
a premise that failed. Accept that it may need migrating — a migration of one record
type is cheaper than an abstraction built before anyone knows whether the feature is
worth having. The G9 fix above is the argument in miniature.

### 10.3 The one thing this note still lacks

Every rung of the ladder is justified by the claim that **an authored summary is more
useful than a distilled one**, and that claim has never been tested — not once, on any
session. It is highly plausible (§5.1's reasoning about intent, dead ends and
uncertainty is sound, and the 68%/1.6% cost figures are measured), but it is the
premise the entire design rests on and it is currently an assertion.

It is also cheap to test *before* building anything: take three sessions that already
have distill digests, have an agent write a wrap-style summary for each from the same
transcript, and read them side by side. No schema, no CLI, no store changes. If
authored summaries do not obviously win, decisions 1, 2 and 6 change shape entirely.

Given that this note's own history is two rounds of careful design reasoning being
corrected by one cheap experiment, running that test first is the recommendation.

## 11. The §10.3 experiment RAN — results, and three findings that change the plan (2026-08-04)

Claude, appended after running the test §10.3 asked for. Verified against `main@3642bd9`
(unchanged). §§1–10 are left intact. **Read 11.1 before quoting any result from this
section elsewhere: the experiment did not measure what it was designed to measure.**

The test §10.3 specified was run on 2026-08-03 as a live 2-project A/B — wrap arm
`project-a` (sessions ended with a new `/nb-wrap` skill), control `project-b`.
Three sessions produced paired authored/distilled summaries: `f4d70ea9`, `d53ef8b6`,
`9822fafc`.

### 11.1 It did not isolate authorship — four dimensions differed at once

The setup claimed "the single variable is authorship" and this note's §5.1 reasoning
assumed both arms have near-identical information access. **Both claims are false**, and
the first conclusion drawn from the run (recorded in the vault packet as a null result)
overstates what was shown:

| | arm A (authored at wrap) | arm B (distill) |
|---|---|---|
| **Model** | `claude-opus-5[1m]` (recorded in each file's frontmatter) | **`claude-opus-4-8[1m]`** — measured by running `claude -p` under `_ISOLATION_ARGS` and asking; `claude_cli.py` passes **no** `--model`, so it takes the CLI default, and `--setting-sources ""` means `~/.claude/settings.json`'s `model` never applies |
| **Prompt** | `~/.claude/skills/nb-wrap/SKILL.md` — an authoring instruction | `DISTILL_SYSTEM` — a *compression* instruction ("terse and information-dense"), wrapped in injection defense |
| **Input** | the live context window | a lossy render — see 11.2 |
| **Output budget** | 6,000 chars **advisory**, self-budgeted | 6,000 chars **hard**, tail-cut — see 11.3 |

What the run therefore established is narrower than "authored summaries do/don't win": an
older model, given a lossy view and a compression prompt, enumerated **more discrete open
items** than the session owner did (`## Unresolved` items 14 vs ~12, 13 vs 9, 14 vs 6 once
the output cap was lifted), while the authored version was **2–4× denser** (~850–921 chars
vs 1,673–3,207) and better prioritized. Item count was used as a proxy for quality; it is a
poor one. **Decisions 1, 2 and 6 should not be re-shaped on this result alone.**

**What a rerun can and cannot fix.** Matching model, prompt and output budget is a ~20-line
change to the harness used here (`third-arm.py`, which patches `DIGEST_MAX_CHARS`, stubs
`_cache_read`, and passes `write_cache=False` so the real digest cache is untouched). That
would control **three of the four** rows above and support a real conclusion: *given the
same model, prompt and budget, does reading a rendered transcript produce a worse summary
than having lived the session?* It would **not** isolate authorship, because the input row
remains different — arm A still has the live context window and arm B still has
`_render_claude`'s view.

**And that residual is not a flaw in the harness; it is the definitional problem.** Holding
input constant means feeding the authoring arm the same render, at which point it is no
longer the session owner and "authorship" has been designed out of the experiment. So
"authorship" as used in §§4-5 is not a single variable at all — it bundles *privileged
input* (thinking, untruncated results, the user's intent in real time) with *authorial
stance* (writing for one's successor rather than compressing for a program). Those can be
separated, but only by deciding which one the design is actually betting on:

- If the bet is **privileged input**, the testable claim is "a summary written with access
  to thinking beats one written without". 11.2 establishes the access asymmetry is real and
  structural; whether it *matters* to output quality is unmeasured.
- If the bet is **authorial stance**, that is testable *without* authoring at all — and it is
  the cheaper experiment. Specification below, because getting it wrong is easy in exactly
  the way 11.1 has already been wrong once.

**The stance test, specified.** Two freshly generated arms, both consuming the **same frozen
render** of the same session, holding constant: model/backend, chunking, redaction, output
cap (generous enough that neither arm is truncated — see 11.3), and the evaluation method.
The single difference is framing: *compress for a downstream program* versus *write for your
successor*. Requirements learned from what went wrong before:

- **Do not compare against the existing digest.** It was produced under a different model
  and the hard 6,000-char bound, both already identified as confounders. It can be shown
  alongside as historical context, clearly labelled, but it is not one of the causal pair.
- **Do not reuse `nb-wrap`'s SKILL.md verbatim.** It is an *operational* skill: it defines
  the treatment as writing from the live owner's knowledge, forbids reading a prior summary,
  and instructs the agent to resolve a session id and write a vault file
  (`~/.claude/skills/nb-wrap/SKILL.md`). Fed a rendered transcript it would conflict with its
  own premise and change several things at once. Use a **pure-output stance prompt adapted
  from it** — successor-oriented framing, same four headings, no file-writing, no
  session-resolution steps.
- **Keep the untrusted-data fence.** `DISTILL_SYSTEM` wraps the transcript in
  `<<<BEGIN TRANSCRIPT … >>>END TRANSCRIPT` and instructs that everything inside is data,
  never instructions. A stance prompt that drops it is not a one-variable change, and it
  reintroduces the S-cf5 role-hijack class.
- **The authored file is an external benchmark, not an arm.** It differs on input as well as
  stance, so it belongs beside the results as a ceiling reference, not inside the comparison.

Running that first is the recommendation: if successor-oriented framing over the same render
captures most of the benefit, the authoring rung is not what buys it.

### 11.2 The strongest evidence FOR §5.1 is a code fact, not a reading

§10.3 called §5.1's premise "highly plausible … but currently an assertion". For one
specific half of it, it is better than an assertion — and the evidence is structural, in
`_render_claude` (`curator/distill.py:156-188`). What the render does, by event and block
branch:

| block | treatment |
|---|---|
| assistant `text`, user `text` | **retained** (redacted) |
| assistant `thinking` | **ignored — never rendered at all** |
| assistant `tool_use` | reduced to one line (`_tool_use_line`, `:139-153`), retaining *selected* arguments where present — `command`, `file_path`, `path`, `pattern`, `query`, `url` — redacted and truncated |
| user `tool_result` | **retained**, each redacted then truncated to `DISTILL_RESULT_TRUNC = 2_000` chars (`:171-180`) |
| `summary` events | retained as `[compact summary] …` |
| sidechain turns | recognized and included, tagged `(subagent)` — the tag is appended on the user/assistant branches; the `summary` branch (`:166-168`) does not append it, so a `summary` event marked `isSidechain` would be kept untagged |
| any other block type | ignored (only the branches above are handled) |

So the precise claim is narrower than "distill can't see the work": tool evidence *does*
survive, in compressed form, and tool results can carry substantial payload text. **What
distill structurally cannot see is the agent's thinking** — and that is exactly where "why
a path was rejected" lives when the rejection was never written out in prose. For that
half of §5.1, the gap is not a tendency distill might overcome with a better prompt; it is
information absent from its input by construction.

**What that does and does not license.** It establishes that **the two arms have materially
different input and that distill lacks this channel entirely**. It does *not* establish that
the wrap-time author holds a strict superset of the render, and an earlier draft of this
section overclaimed exactly that. The author's input is its context window at the wrap turn,
which is not the same object as the transcript: on a long session, compaction can leave the
author holding *less* early detail than the render preserves, so in general neither side
contains the other.

For these three pairs specifically the render shows **no `[compact summary]` events at all**
(0 in each of the 217,208 / 440,042 / 464,119-char renders), so no compaction was recorded
and the author plausibly did retain the whole session — but that is a property of these
sessions under a 1M-context model, not a general guarantee, and it says nothing about how
much of that context the model actually used. So the direction of advantage is *plausible
and unmeasured* here, not demonstrated. Whether the author can exploit the extra channel
remains part of the untested premise; only the exclusion mechanism is established.

**A related worry is also retired, but on measurement rather than on the render's shape.**
An authored summary written *inside* the session does not meaningfully leak into that
session's own digest: of the authored file's long lines, **0 of 40, 1 of 63, and 0 of 33**
appear in the render for the three pairs. That is the evidence for arm B's independence —
*not* a claim that all tool payloads are dropped, which the table above shows is false. The
mechanism is that a `Write` tool call is reduced to `_tool_use_line`'s selected arguments
(so the file *path* survives, the body does not). Only skeleton leaks: frontmatter keys and
heading names, via the injected SKILL.md text.

### 11.3 A defect made the first comparison misleading — `G11` candidate

`_bound()` (`curator/distill.py:229-236`) hard-caps a digest at
`DIGEST_MAX_CHARS = 6_000` and truncates the **tail**, appending `[digest truncated]`.
`## Unresolved` is the **last** section, so on dense sessions it is deleted outright.

- Across all 26 digests in the live store: **17 hit the cap, and 15 of those have no
  `## Unresolved`; all 9 under the cap have one.**
- Uncapped, the same three transcripts produce **13,173 / 10,317 / 16,065** chars —
  1.7–2.7× the cap. The cap is far too small, not marginally small.
- **A bigger cap alone is not the fix.** At a 9,000 cap all three were still truncated and
  section coverage was 4/4, 3/4, **2/4** — given 50% more room the model wrote more
  early-section prose rather than reaching the tail. It writes exhaustively in section
  order and never budgets to reach the end, so a real fix budgets per section (or orders
  sections by importance) as well as raising the ceiling. The cap itself is deliberate
  (S-cf5 overran an advisory cap); the bug is the tail-first cut against a fixed order.

**This also corrects a claim made against this note.** The vault packet argued §5.1 was
weak because digest `2bf587ba` "already contains all three" of its claims. It does not
contain the third: `2bf587ba` has no `## Unresolved` section, ends mid-sentence under
`[digest truncated]`, and greps **zero** matches for uncertainty language. §5.1 was better
supported than the objection assumed — the uncertainty axis was absent because the cap ate
it.

### 11.4 The *consumption* half is already free; the ingestion half is not — this narrows decision 2

Decision 2 currently recommends "a CLI command, keyed by `session_id`". That command is still
needed, but it is thinner than it looks, because **the path from an authored body to curated
facts already exists and needs no Neurobase code change**:

- `store.write_raw(..., transcript_path=None)` writes a **v1 raw**.
- `_distill_one` returns `None` for a v1 raw ("no pointer ⇒ skim") — **with no brain
  call**, returning before any cache, render or brain work (`distill.py:321-324`).
- `distill_docs` then appends the document **unchanged** (`:429-432`), so the curator plans
  over the raw's **body**.

What follows separates that verified passthrough from the ingestion work it does *not* cover.

So **once a body is in a v1 raw, the fold consumes it as-is** and it reaches the next
session through the existing `SessionStart` recall injection. No distill, no transcript
read, no digest cache — nothing that can expire, which is why this shape also **retires
`G10` for wrapped sessions**. One qualification even here: an exceptionally large body can
still be stripped or truncated by the plan-payload assembler (`curator/engine.py:145-171`),
so "the fold sees the body" is bounded by `plan_payload_max_bytes`.

**What is NOT already built — and an earlier draft of this section got this wrong.** It
claimed `write_raw` is a "session-keyed overwrite" that would *replace* the session's
deterministic skim, so no ingestion work was needed. That is false as stated:

- **The raw's identity is its `captured_at`, not its `session_id`.** `raw_filename`
  (`core/store.py:200-211`) derives the filename from the `(captured_at, agent, session_id)`
  tuple at microsecond precision. Its own comment states the constraint exactly: the
  overwrite trick holds for *"a scribe reusing a **stable** `captured_at`"*.
- **The scribe does not reuse one.** `adapters/claude/scribe.py:347` passes a fresh
  `datetime.now(UTC)` on every capture.
- **Live evidence, which this note previously misread as redundancy:**
  `project-b` holds **four separate raws for one session** (`499ca1a4`), at four
  different timestamps. Same session, four files.

So a wrap-time write at a different timestamp **adds** a second raw for the session; the
later `SessionEnd` capture does not overwrite it and the fold would see the session twice.
Replacing an existing unconsumed raw requires: discovering that raw, reusing its exact
`captured_at`, ordering the write against `SessionEnd` capture, and coordinating through the
project lock — and **no current CLI or ingestion caller offers that operation**. A further
boundary: calling `store.write_raw` directly writes the supplied body as-is, bypassing the
scribe's D13 per-value whole-raw redaction, which the spec requires
(`docs/neurobase-spec-appendix.md:1292-1301`).

**Corrected conclusion.** The *passthrough* is free and verified; the *ingestion* is not
built. Decision 2's CLI command is therefore still needed — but it becomes a thin verb over
an existing consumption path rather than a new subsystem, and its real work is identity,
ordering, locking, redaction and provenance rather than anything about digests. Any
proof-of-concept that skips those by calling internals directly should be labelled a one-off
harness, not a demonstration of an existing path. This still supports decision 8's "derive,
don't duplicate" — one act of authorship, two destinations — it just costs more than the
earlier draft claimed.

**Four further caveats.** (1) An LLM-authored raw breaks the invariant that capture is
deterministic; doing it properly wants an `authority: agent-authored` field so downstream
can distinguish the two, which is decision 6's schema-1 slice. (2) `projects/*/sessions/` is
gitignored in the vault (`.gitignore:22`), so authored files are currently **not durable** —
the three from this experiment exist on disk only. (3) An already-consumed session raises
`RawConsumedError` and needs a fresh `captured_at` — which by the above means a *new* raw,
not a replacement. (4) Codex sessions cannot distill (`render_transcript` returns `None` for
any non-`claude` agent) and will not author either unless Codex's own wrap performs the same
write, so ~4 sessions per project per day stay as skims.

### 11.5 Continuous curation: the batching is structural, and authoring is what removes it

New to this note, and the operational reason the authoring rung matters beyond summary
quality. Curation currently arrives as an end-of-day hour-long batch. Three mechanisms
cause that, and the first two are configuration:

1. **`stale_hours: 12`** — hook-spawned `curate --if-stale` refuses to run until the oldest
   unconsumed raw is over 12 h old, so nothing drains during a working day. This is the
   pile-up.
2. **The auto-tier budget is internally inconsistent.** `auto_max_raws: 40` against
   `auto_max_seconds: 900`. Per-capture distill was **measured at 1.8–2.3 min** (2026-08-03,
   27 captures across two projects), so 900 s covers **~7 captures**, never 40. The config
   comment concedes the gap — *"A healthy 40-raw pass is estimated at 4-10 min (per-call
   latency is **NOT measured** — an open item)"*. It is now measured, and the estimate is
   off by roughly 8×.
3. **Exhaustion has two different outcomes, and an earlier draft of this section merged
   them into one wrong claim.** It said any `BudgetExhausted` still commits the batch from
   skims, so a clock overrun quietly drained the day at lower fidelity. That is true only
   for the distill-side ceilings, **not** for the clock:
   - **`max_brain_calls` / `max_distill_chunks` (distill-side): skim-and-commit.**
     `distill_docs` appends the remaining originals and breaks
     (`curator/distill.py:413-422`), and because these ceilings hold back a **planning
     reserve** (`distill_allowance = max_brain_calls - reserve_calls`), the plan call can
     still spend it. A batch commits and the remainder is folded from deterministic skims —
     lower fidelity, but progress. Pinned by `tests/test_curate_budget.py:216-265`.
   - **`max_seconds` (the clock): nothing commits at all.** `PassBudget.debit` calls
     `_check_clock()` **first, before every ceiling check** (`curator/budget.py:137-162`),
     so once the deadline passes *every* later debit raises — the reserve is irrelevant. The
     first plan call raises, `engine.py:438-444` breaks, and the selected raws stay
     **unconsumed** and are reported in `unconsumed_left`. Pinned by
     `tests/test_curate_budget.py:499-539`, which asserts `plan_calls == 0`,
     `batches == 0`, `status != "error"`, and all 3 raws still unconsumed.

   So a clock overrun **during distillation** is a visible, retryable no-progress
   condition, not a silent downgrade: the pass reports a bounded stop, consumes nothing,
   and leaves the backlog intact for the next attempt. That is a *better* failure mode than
   the earlier draft assumed — but it also means a mis-sized auto tier burns 15 minutes of
   brain latency per pass and commits nothing, repeatedly, which is its own kind of waste.

   One scope limit on "nothing commits": that is the branch this section is about — the
   deadline expiring while distilling, which is what the zero-plan-call test pins. If the
   deadline is instead first observed on a *later* plan or synthesis debit, batches already
   committed earlier in the pass **stay committed** (`curator/engine.py:425-514`); the stop
   is still clean, it just isn't all-or-nothing.

**Authoring at wrap deletes the expensive step rather than rescheduling it.** With a v1 raw
(11.4) there is no distill call at all, so a per-session pass costs only the fold — 1–2
brain calls, a minute or two — run at wrap time, when the session is ending anyway.
Measured support: the `project-b` pass spent `budget_calls: 2` in total for 10
already-cached digests plus its fold.

**Only one of the two config knobs is safe to state as a value.** `stale_hours` is the
batching gate and lowering it is unambiguous:

```toml
[curate]
stale_hours = 1      # was 12 — drain continuously instead of batching a day
```

**`auto_max_raws` cannot be given a value that guarantees the clock, and an earlier draft of
this section claimed one.** It offered `auto_max_raws = 8` as "a pass that FITS its clock and
always finishes". That contradicts this section's own measurements. The arithmetic, against
`auto_max_seconds = 900`:

| | per-capture | 8 captures | remaining for the fold |
|---|---|---|---|
| measured low | 1.8 min (108 s) | 864 s | **36 s** — less than this section's own "minute or two" fold estimate |
| measured high | 2.3 min (138 s) | 1,104 s | **already over the ceiling before the fold** |
| observed worst single capture | 3 m 32 s (212 s) | — | one capture is 24% of the whole budget |

So eight can still expire mid-distill and commit nothing — the exact failure the value was
supposed to prevent. Working backwards instead, with a 180 s reserve for planning plus
synthesis, 720 s at the ~2 min median admits **~6** captures, and at the observed worst case
admits **3**. Those differ by 2×, which is the point: **no fixed raw count can guarantee a
wall-clock outcome across a 10 s – 3 m 32 s latency spread.** Any number here is a heuristic,
so treat a lowered `auto_max_raws` (4–6 on this machine's numbers) as an unvalidated
experiment to be checked against real pass outcomes, not as a fix.

**This note does not specify the fix — deliberately.** Two successive drafts proposed one and
each promised an outcome its mechanism could not deliver (first a raw-count that "always
finishes", then a p90 admission check that "would convert" a zero-commit expiry into "fold
what fit, defer the rest"). Both were guarantee-shaped claims about *estimated* quantities.
What follows is therefore limited to observations and the constraints any real fix has to
satisfy; choosing and specifying the mechanism is design work beyond this note.

**Observations, all verified against `main@3642bd9`:**

- The budget is a *hard stop*, not an admission controller. `_check_clock` refuses the next
  debit once the deadline has passed, but nothing prevents the pass from *starting* a
  3-minute distill with 30 seconds left.
- **Distillation runs over the whole selected list before planning begins.**
  `distill_docs` is called on all of `select_raws`' output and its return becomes `remaining`
  (`curator/engine.py:398-409`), so there is no per-item interleaving of distill and fold.
- **A distill-side budget stop does not defer the remainder — it downgrades it.** On
  `BudgetExhausted`, `distill_docs` does `out.extend(docs[index:])`
  (`curator/distill.py:413-422`), so the *undistilled* raws continue into planning carrying
  their deterministic skims, and get consumed at skim fidelity.

**Constraints on any fix, from those three facts:**

- A latency-based admission check (p90 or otherwise) can only ever be **probabilistic risk
  reduction**. A tail-latency distill still overruns the estimate, after which the next
  planning debit fails exactly as mechanism 3 describes. It cannot be stated as a guarantee.
- "Fold what fit, defer the rest" is **not reachable from configuration or budget accounting
  alone.** Because the undistilled suffix is appended as skims and flows into planning,
  deferring it requires a change to the distill→engine handoff (return the undistilled suffix
  separately so the engine can exclude it from `remaining`) or to selection, so those raws
  stay unconsumed rather than being folded at skim fidelity.
- Whatever is chosen, the auto tier's sizing should come from *measured* latency rather than
  the estimate its own comments admit is unmeasured.

Note also that skim-fidelity folding is not obviously the wrong behaviour — it is the
deliberate D16 "distill never aborts a pass" design, and it does drain the backlog. Whether
deferring is preferable to downgrading is a product decision this note is not in a position
to make.

Two smaller notes. Raising `auto_max_seconds` instead would let a detached background curator
run for over an hour, which is the thing being escaped. And `distill: "off"` is already
supported per-store, which becomes reasonable for a project where every session authors its
own digest — in which case none of this arithmetic binds, because there is no distill call to
schedule. **That is the real relief valve here: the authoring rung removes the per-capture
cost that makes the clock a problem at all; the knobs above only matter for sessions that go
unwrapped.**

### 11.6 What §11 changes in §10.2's recommendations

| # | Decision | §10.2 said | §11 |
|---|---|---|---|
| **2** | How the summary reaches the store | CLI command keyed by `session_id` | **Still needed — narrowed, not replaced** (11.4). A v1 raw's body passes through the fold with no distill call, so the *consumption* half is free; but the raw's identity is `captured_at`-derived, so replacing a session's skim needs identity discovery, ordering against `SessionEnd`, the project lock, and the D13 redaction boundary. The verb is thin, its work is provenance and concurrency |
| **6** | Session slice schema-1-only | Schema-1-only | **Unchanged, and now more urgent** — the `authority: agent-authored` field in 11.4 is exactly this slice, and it is what makes 11.5's continuous loop honest about provenance |
| **1** | Smallest first step | Rungs 1+3, durable session record | **Amend:** the durable-record half is blocking, not optional — `.gitignore:22` means authored summaries are currently destroyed by a vault clean |
| **7, 8** | `VaultProvider` personal; derive don't duplicate | — | **Unchanged.** 11.4 supports 8 rather than complicating it |
| — | *New:* file `G11` | — | **Do this first.** It is independent of every decision here and biases every dense session's digest today, wrap arm or not. Note its fix is a section-aware `_bound()`; **raising the 6,000 ceiling is a spec + ADR change**, not tuning |

**And the premise itself is still not settled.** 11.1 means §10.3's question remains open;
11.2 means one half of the *mechanism* §5.1 proposed is real and structural. Those are
compatible, and the distinction is worth stating precisely, in three steps rather than two:

1. **Established:** distill's input never contains the agent's thinking. The channel is
   absent by construction, not by tendency.
2. **Not established:** that the wrap-time author therefore holds *more* input. Its context
   at the wrap turn is not the transcript, and compaction can cost it detail the render
   keeps. For the three pairs here no compaction was recorded, so the direction is plausible
   — but plausible is not measured.
3. **Not established:** that any input advantage produces a *better* summary. That is
   §10.3's original question, and it is still open.

**The next test should be the stance test as specified in 11.1**, not a "clean" authorship
test — 11.1 explains why no rerun can isolate authorship while holding input constant, since
matching input removes the very thing under test. The stance test is cheaper, isolates one
variable, and needs two freshly generated arms over one frozen render with an adapted
(not verbatim) stance prompt that keeps the untrusted-data fence. It has not been run.

## 12. ⚠️ EXPERIMENT BOUNDARY — the control arm's generator changed on 2026-08-05 (Claude)

**Any comparison that spans this date is comparing two different control arms.** Segment
results at 2026-08-05; do not pool them.

The `neurobase` on `PATH` is a uv-tool *copy*, and it had silently fallen **16 paths behind
the worktree** — including **`curator/distill.py`, 236 changed lines**. That file is the
automatic digest generator, i.e. exactly the control arm of the §11 A/B
(`project-b` = distill, `project-a` = `/nb-wrap` authored). So every distill digest
produced before 2026-08-05 came from an *older generator* than the source this note reasons
about, and §11's readings were taken against that older binary.

**Reinstalled from the worktree on 2026-08-05** (`uv tool install --force`, source `main` @
`961f8d5`); a tree-wide `diff -rq -x '__pycache__'` is now clean and `doctor` passes 17/17.
Andrew's call was *reinstall now and stamp the date*, accepting a segmented control arm over
a stale binary.

**Asymmetric on purpose, and that is the hazard:** the wrap arm is unaffected — `/nb-wrap`
authors its digest outside the CLI — so the change lands on **one arm only**. A shift in
results across this boundary is therefore confounded with the generator swap and cannot be
attributed to authorship.

⚠️ **§11's cost figures are in the older-binary regime too** (`curator/{engine,budget}.py`
also differed, 64 and a handful of lines). Re-measure before quoting them as current, and
note the brain remains pinned to `codex-cli`, so `brain_usage` is still null either way.

**How it went unnoticed:** the staleness check in `/nb-check` and the vault packet diffed
`cli/__init__.py` **alone**, which was byte-identical throughout — a true statement implying
a false one. Both copies also report `0.1.0`. The check now diffs the tree; that fix is what
surfaced this.

## 13. Decision 8 brief — what `neurobase wrap` is, and whether it should exist (2026-08-06, Claude)

Written because §11.4 established that decision 8 is the **gate**: nothing about the
authored-digest verb can be built until it is answered, while decisions 1, 2 and 6 are
either settled (ADR-0027) or narrowed to work that only makes sense once this is.

**State on `main` @ `087a632`, verified not assumed:** no `wrap` verb in
`cli/__init__.py`; no `authority` field in `core/store.py`. **Nothing is implemented.**
Three authored digests exist, in `~/vault/projects/project-a/sessions/`, and `.gitignore:22`
means they are **not durable** — a vault clean destroys them.

### 13.1 The question, stated so it cannot be answered by preference

`/wrap` already writes a durable session summary to the vault packet: same moment, same
agent, same session. Decision 8 asks what a Neurobase-side `wrap` does about that. It is
**not** "should the agent author its own summary" — that is decision 1, and the authoring
already happens. It is: **does the authored text get written twice, derived once, or not
stored in Neurobase at all?**

### 13.2 The three options, with what each costs and what each forecloses

| | **A — derive** (`wrap --summary -`, `/wrap` pipes what it just wrote) | **B — independent verb** (`neurobase wrap` authors its own) | **C — don't build it** (drain distill continuously instead) |
|---|---|---|---|
| Acts of authorship | **one** | **two**, on the same session | one (distill's) |
| Drift between destinations | impossible by construction | needs a stated precedence rule — **none has been proposed** | n/a |
| Neurobase without `/wrap` | needs a caller that pipes; a bare `neurobase wrap` has no text | works standalone | works today |
| Cost win from 11.4 (no distill call for a v1 raw) | **yes** | yes | **no** — distill still runs, just sooner |
| G10 (transcript expiry) | retired for wrapped sessions | retired for wrapped sessions | narrowed, not retired — `stale_hours` shrinks the window |
| Ingestion work required (11.4) | identity, ordering vs `SessionEnd`, project lock, D13 redaction, `authority` provenance | identical | **none** |
| Forecloses | little — B remains reachable later by adding an authoring path | the "one artifact" property, permanently | the authored-digest subsystem, until re-decided |

### 13.3 What the evidence actually supports

- **For building something:** the cost mechanism is a **code fact**, not a reading. A v1
  raw returns from `_distill_one` at `distill.py:321-324` before any cache, render or
  brain call, and `distill_docs` appends it unchanged — so an authored body reaches the
  fold with **no distill call at all**. §12 voids the *magnitude* figures (they were taken
  against the 236-line-stale `distill.py`), but not this.
- **Against building much:** the *quality* premise remains unestablished at step 3 of
  §11.6's three — distill's input provably lacks the agent's thinking, the author
  plausibly holds more, and *neither* implies a better summary. **If the win is only cost
  and timing, C is the correct answer** and `stale_hours = 1` collects most of it for one
  config line.
- **The A/B cannot settle it as it stands.** §12: the control arm's generator changed
  mid-flight, asymmetrically. The instrument that would answer §10.3 is the **stance test
  in 11.1**, which has not been run.

### 13.4 Recommendation, unchanged from §10.2 and now better supported

**A — derive, don't duplicate.** `/wrap` is the only caller that needs to exist; two
destinations for one act of authorship is precisely the failure §1 documents. It is also
the option that keeps B reachable: adding an authoring path later is additive, whereas
recovering the "one artifact" property after shipping B is not.

⚠️ **But sequence it behind two cheaper things**, because A's ingestion half is where the
work actually is (11.4) and neither of these depends on the answer:

1. **Fix the durability hole.** `.gitignore:22` currently destroys authored digests. Every
   experiment result rests on files a `git clean` removes.
2. **Run the stance test (11.1).** It is the only cheap instrument that isolates one
   variable, and it can retire this entire subsystem — the outcome §10.3 calls the most
   valuable one.

If the stance test shows no advantage, take **C** and keep the config change. If it shows
one, **A**'s ingestion work is then justified by a measured result rather than by a
mechanism that only proves the *cost* half.

### 13.5 What "yes" to A commits to

ADR-0027 is `Accepted` and immutable, so D47's requirements are acceptance criteria, not
suggestions: the spec-appendix update, the `_raw_payload` exclusion, and the paired
captured/authored fixtures. Plus, from 11.4: reuse of an existing raw's exact
`captured_at` (identity is `(captured_at, agent, session_id)` at microsecond precision —
`core/store.py:200-211`), ordering against the `SessionEnd` capture, the project lock, and
routing through the scribe's D13 redaction rather than calling `store.write_raw` directly.
`RawConsumedError` on an already-consumed session means a *new* raw, not a replacement.

## 14. ⚠️ SECOND CONFOUND — the distill arm does not run for Codex captures (2026-08-06, Claude)

**Measured, not inferred.** A `neurobase curate` pass run and observed on 2026-08-06 emitted
the first `distill_detail` readout this note has had access to:

```
raw: 8, backlog: 8, distilled: 0, fallback: 8, upserts: 1, active_facts: 16, budget_calls: 2
distill_detail: {"fallback_no_pointer": 1, "fallback_unsupported_agent": 7,
                 "fallback_transcript_gone": 0, "fallback_empty_render": 0, ...}
```

`render_transcript` (`curator/distill.py:200-205`) has a renderer for **`claude` only** and
returns `None` for every other agent; `_distill_one` turns that into `unsupported_agent`
(`:495-499`). **This is deliberate and already documented** — the code cites ADR-0013 S-cf3,
the ADR records Codex activity extraction as a deferred follow-up needing "a separate
bounded format contract" (`docs/adr/0013-capture-fidelity-event-shapes.md:33-34`), and
`docs/known-gaps.md:1249` already carries the row. **No new gap entry is owed, and filing
one would duplicate.**

**What is new is the coverage consequence.** `known-gaps` documents `unsupported_agent`
inside a *cost model* argument — one of five zero-call paths explaining why "each raw costs
exactly one call" is false downward. It is recorded as accounting. Nobody had measured what
fraction of a real store it covers:

| store | claude | codex | codex share |
|---|---|---|---|
| `neurobase` | 5 | 21 | **81%** |
| `project-a` (wrap arm) | 41 | 22 | 35% |
| `project-b` (control arm) | 25 | 35 | **58%** |

Tier-2 distill is dark for four of five captures in this store, and — the part that matters
here — **the control arm is the more Codex-heavy of the two A/B projects.** For 58% of
`project-b`'s sessions the "distill arm" was never distill; it was the deterministic
skim. Any store-level comparison of the two arm projects is therefore
authored-vs-*mixed(distill|skim)*, with the mixture differing between arms.

### 14.1 Scope — what this does NOT claim

**§11's three pairs are unaffected, and were checked rather than assumed.** `f4d70ea9`,
`d53ef8b6` and `9822fafc` are all `agent: claude` with a resolving `transcript_path`, so arm
B genuinely distilled for all three; §11.1's table already says arm B saw "`_render_claude`'s
view". Nothing in §11 needs re-reading because of this entry, and the null result recorded in
the vault packet is not weakened by it.

This entry also does not claim the Codex renderer *should* be built, does not propose a
format contract, and does not assert that skim-vs-distill materially changes curated quality
— that comparison has never been run either.

### 14.2 Second-order: two known gaps govern a path most captures never take

`G10` (transcript expiry) and `G11` (fixed-order prefix cap) both concern the distill path.
In this store that path is reached by 5 of 26 raws. This does not make either gap wrong —
both were derived from the code, and the claude-captured minority is real — but it does mean
their *observed* incidence here will understate their severity in a claude-dominant store and
overstate their relevance to this one. Worth stating wherever either is quoted with a
frequency.

Corroborating: all 25 pointer-bearing raws in this store still resolve to an existing
transcript file (checked 2026-08-06), so **`G10` has not yet fired here even once** —
`fallback_transcript_gone: 0`. The 7 codex fallbacks were not expiry and not missing
pointers.

### 14.3 The wrap verb's cheap path is confirmed live

`fallback_no_pointer: 1` in the same pass **is** the ADR-0027 authored raw from that
morning's `/wrap` Phase 3.5. It is the only raw in the store with no `capture_version` and no
`transcript_path`, and it took the early return in `_distill_one` before any cache, render or
brain call. §13's cost argument is now a live observation rather than a code reading. Note
this is the *reason code*, not `distilled == 0` — the distinction the discarded test could
not make.

⛔ **CORRECTED an hour later — the double-raw residual is UNTESTED, not disproven.** This
entry first read "no `SessionEnd` skim was written for that session, so the double-raw case
did not occur." **That inference was wrong.** No skim was written because **capture silently
failed on `cwd`** (§14.5): session `66b54a2e`'s transcript sits in the *parent* project
directory, which is not a registered root. The absence of the second raw was an artifact of a
bug, not evidence about the residual. Nothing here bears on whether the residual matters.

⭐ **One thing does survive the correction, and it favours A.** The authored raw landed
*anyway*, because `/wrap` Phase 3.5 passes the repo directory to `neurobase wrap` explicitly.
**The authored path is robust to a `cwd` bug that silently kills the capture path** — an
argument for A that nobody had made, discovered only because both ran on the same session.

The fold produced `upserts: 1` and `active_facts: 16 → 16`: the authored digest updated
exactly one fact — `session-summary-layering-direction`, the one its content bore on — and
created none. That is arguably correct behaviour rather than a failure, but it is the first
datum on the quality premise and it is not yet evidence for A.

### 14.4 ✅ ANSWERED — hook-spawned passes die on an unregistered `cwd`

The pass above drained a 27-hour backlog in **47 seconds**, exit 0, while `is_stale` had been
open ~15 hours (oldest unconsumed `2026-08-05T13:19:48Z`, `stale_hours = 12`) with no journal
line since `2026-08-05T13:20:35Z`. Not cost, not lock contention, not the brain.

**`curate` resolves the project BEFORE it checks staleness**, so from a cwd that matches no
registered root it exits 1 having never looked. Demonstrated with a control:

```
--cwd "…/AI Projects/Neurobase"            → "Not an enabled project…"          EXIT=1
--cwd "…/AI Projects/Neurobase/neurobase"  → "Not stale — nothing to curate."   EXIT=0
```

`spawn_curate_if_stale` (`adapters/recall_common.py:159-169`) routes stdout and stderr to
`DEVNULL` and wraps the `Popen` in `contextlib.suppress(OSError)`, so this failure is silent
by construction — there is no surface on which it could ever have been noticed.

⚠️ **This weakens C more than "unverified" did.** C ("don't build it; `stale_hours = 1`, drain
continuously") assumes the hook-spawned pass fires. In this repo it has **never** fired.

### 14.5 The real defect is upstream of curation: capture is silently lost too

`scribe` resolves through the same `resolve_or_auto_enable(root, cwd, …)` and returns `None` —
a silent no-op — when the cwd does not resolve (`adapters/claude/scribe.py:294-301`). Measured
2026-08-06:

| | count |
|---|---|
| Claude sessions with cwd = **parent** (`…/AI Projects/Neurobase`) | **28** |
| Claude sessions with cwd = the nested repo dir | 4 |
| raws in the store carrying the parent cwd | **0** |

The store's 26 raws come from the nested dir (4), Codex review worktrees under
`~/vault/outputs/reviews/` (19), and `nb-phase-g` (1). **≈28 Claude sessions on this project
produced no memory at all**, silently.

**Why this repo and not the others:** `neurobase` is registered at
`…/AI Projects/Neurobase/neurobase` — a repo nested one level inside a workspace folder whose
parent is **not** a git repository, so there is no `git-common-dir` collapse to rescue it.
`project-a` and `project-b` are registered at their top-level repo directories,
which match their Claude transcript directories exactly. **The 58% / 35% A/B shares in §14 are
therefore unaffected by this bug.**

⚠️ **But the 81% Codex share for `neurobase` IS substantially an artifact of it** — the Claude
sessions that would have balanced that store were dropped before capture. Quote the confound
for the A/B arms; do not quote 81% as a fact about how this project is worked.

**Not in `known-gaps`** (the only match is a CLI-message inventory row at `:1382`), so this is
a candidate **`G16`**. The defect worth filing is the *silence*, not the resolution rule:
capture, injection and curation all no-op identically whether a repo is deliberately not
enabled or accidentally addressed one directory too high.

---

## 15. Decisions 1 and 2 — measured, not argued (2026-08-07, Claude)

> ⚠️ **SUPERSEDED IN ITS FRAMING by §16 (same day, later).** §15.3's falsifier was run and
> it inverted the result: the multi-batch split this section treats as the risk is *fine*,
> and the single large batch it treats as the safe baseline is what silently destroys
> extraction. Every number below is accurate; the hazard they are organized around is the
> wrong one.

§6 left decisions **1** (smallest first step) and **2** (how a wrap summary reaches the store)
open, and §10.2/§11.4 recommended answers without evidence for the one risk that mattered:
that authoring a wrap summary *adds* a second raw for a session which `SessionEnd` also
captures, so the fold might see the session twice and duplicate its facts. That residual is
now **measured against the live store** rather than reasoned about. This section reports what
the store actually contains — including two places the earlier, informal read of it was
stronger than the data supports.

### 15.1 The double-raw residual fires, and does not duplicate facts

The store holds **4 raws carrying `authority: agent-authored`**, and **3 sessions hold exactly
2 raws** — an authored v1 and a `SessionEnd` v2 skim. So the residual is real: authoring does
not replace the deterministic capture, exactly as §11.4's corrected conclusion predicted from
`raw_filename`'s identity rule.

**No duplicate facts resulted.** In the two sessions where the merge is observable, the
curator folded both routes into single facts that cite **both raws as provenance**:

| session | facts citing *both* raws |
|---|---|
| `b7c62f8a` | 3 — `adr-0028-typed-record-model`, `vault-provider-rejected`, `vault-session-artifact-durability-collision` |
| `f8083cb5` | 2 — `session-summary-layering-direction`, `unregistered-parent-cwd-diagnosis` |

That is the answer decision 2 needed: **one act of authorship, two destinations, one fact.**
The "derive, don't duplicate" shape survives contact with the real ingestion path.

### 15.2 ⚠️ But "every fact cites both raws" is too strong — the third session says otherwise

The third doubled session, **`66b54a2e`**, shows a different outcome, and it was missed when
this result was first written up:

- the **authored** raw (`16:05:47`) is cited by exactly one fact,
  `session-summary-layering-direction`;
- its **`SessionEnd`** counterpart (`21:30:40`, `capture_version: 2`, 255 lines, `consumed:
  true`, filename carrying a `__g0001` generation suffix) is cited by **no fact at all**;
- and **no fact cites both**.

So across three doubled sessions the outcomes are **merge, merge, and silent
non-contribution** — not a uniform merge. The consumed skim was read, produced nothing
citable, and left no trace saying so.

⭐ **This is a better result for decision 1 than a uniform merge would have been, and a worse
one for observability.** The duplication risk is genuinely absent — nothing double-counted in
any of the three. But the failure mode that *did* appear is invisible: `consumed: true` is
recorded identically whether a raw contributed a fact or contributed nothing, so the only way
to tell them apart is to grep provenance across every curated fact, as this section did. **A
raw that is consumed without contributing is indistinguishable from one that was folded**, and
nothing in the pass record surfaces the difference. That is worth a gap entry of its own; it is
not G8, G10 or G11, and it is not what ADR-0028's `authority` field addresses either.

### 15.3 The falsifying condition, now with a count

Every prior write-up of this result named its own falsifier: the merge depends on both raws
landing in the **same plan batch**, and a backlog large enough to split them across batches
could separate the two routes. The store's `.curator-log.jsonl` now puts a number on how
untested that condition is:

| passes in the log | `batches` | `status` |
|---|---|---|
| 7 | **1** | `ok` |
| 1 | 0 | `error` (the G17 failure — consumed nothing) |

**8 passes, and every single successful one ran at exactly one plan batch.** The condition
under which the merge could fail has therefore *never occurred*, and the evidence in §15.1 is
silent about it rather than supportive. Forcing a multi-batch pass — a backlog beyond
`ONE_RAW_PER_BATCH`, or a lowered cap — is the one experiment that would settle it, and it has
not been run.

### 15.4 Correction: `fallback` is the normal path, not an anomaly

A curate pass observed on 2026-08-07 logged `distilled: 0, fallback: 1`, which looked at first
like the distill step declining on a raw. The log shows it is simply **the ordinary case**:

```
distilled: 2  fallback: 5     distilled: 0  fallback: 5
distilled: 2  fallback: 2     distilled: 3  fallback: 2
distilled: 0  fallback: 7     distilled: 0  fallback: 4
distilled: 0  fallback: 8     distilled: 0  fallback: 1
```

Fallback dominates every pass in the store's history, and §14 already explains why:
`render_transcript` returns `None` for any non-`claude` agent, so **no Codex capture can ever
distill**, and authored v1 raws deliberately skip distillation by design (§11.4 —
`_distill_one` returns `None` for a pointerless raw *with no brain call*). A high `fallback`
count is what a store dominated by Codex captures and authored summaries is supposed to look
like. **Nothing here needs chasing** — recorded so the observation is not re-raised as a defect
by a later reader.

### 15.5 What this closes, and what it does not

- **Decision 2 — answered on the evidence.** The thin CLI verb over the existing consumption
  path is the right shape; the double-raw residual it leaves behind is real but benign at the
  batch sizes this store has ever seen. Its remaining work is still what §11.4 named —
  identity, ordering, locking, redaction, provenance — none of which this measurement touches.
- **Decision 1 — two-thirds closed.** Rung 1 shipped; the durable-record half stopped blocking
  once digests became store raws rather than vault files; **rung 3 is demoted**, because
  ADR-0028's `D50` names `doctor`, not the UI, as the first surface that must read through the
  new abstraction — and that ADR is now `Accepted` and immutable.
- **Not closed:** the multi-batch falsifier of §15.3, and the consumed-but-uncontributing raw
  of §15.2. Both are cheap to state and neither is a blocker; both are invisible without
  deliberately looking, which is the argument for writing them down here rather than trusting
  a later session to re-derive them.

**Method note.** Every number above was generated from the live store on 2026-08-07 —
`raw/` frontmatter, provenance grepped across `curated/`, and `.curator-log.jsonl` — rather
than carried forward from an earlier session's summary of it. Two claims did not survive that:
§15.2's uniform merge and §15.4's anomaly. Both had been recorded as settled.

---

## 16. §15.3's falsifier RAN — and the hazard was the opposite one (2026-08-07, Claude)

§15.3 named exactly one unrun experiment: the merge in §15.1 depends on both raws landing
in the **same plan batch**, and a backlog large enough to split them across batches "could
separate the two routes." It was run later the same day. **Both halves of that expectation
were wrong**, and the second one was a shipped defect.

### 16.1 Multi-batch is fine — the double-raw residual was never the hazard

Provenance **accumulates across batches** on a same-slug upsert: one fact was observed
citing **four raws planned in four separate batches**. The planner sees the existing fact in
each later batch's payload and updates it rather than writing a second one, so splitting the
two routes across batches does not separate them.

⭐ **This discharges §15.3's falsifier outright**, rather than leaving decision 2's residual
"real but benign at the batch sizes this store has ever seen." It is benign for a structural
reason, not a lucky one.

### 16.2 The experiment found the inverse defect instead — `G19`

A plan batch was capped by **bytes** (`plan_payload_max_bytes` = 262,144) but not by **raw
count**. Session captures are a few KB, so a realistic backlog fits many times over and
arrives as **one batch** — a single plan call weighing every raw against every other. Past a
handful it writes nothing, marks every raw `consumed: true`, and reports `status: ok`.

Measured on a **copy** of the live store, `distill=off`, fixture rebuilt per run, using the
10 `codex` raws §15.2 found consumed-but-uncited:

| raws/batch | runs | raws cited | runs producing facts | calls |
|---|---|---|---|---|
| 1 | 6 | 6,7,5,6,6,6 | **6/6** | 11 |
| 2 | 3 | 6,8,**0** | 2/3 | 6 |
| **3** | 3 | 7,7,4 | **3/3** | **5** |
| 5 | 3 | 0,5,1 | 1/3 | 3 |
| 10 | 6 | 0,0,4,0,0,0 | **0/6** | 2 |

⛔ **So §15.3 had the risk exactly backwards.** It treated "one batch" as the safe baseline
and the split as the thing that might break the merge. The split is harmless; the single
large batch is what silently extracts nothing — and all 8 passes §15.3 counted had run at
exactly one batch, which is precisely why the store's own history could not show it.

**Filed and fixed as `G19`** (PR #33 → `3117ad4`), which caps a batch at
`plan_max_raws = 3`. ⚠️ 3 is **calibrated** against a store with 21 active facts sharing the
plan payload, not derived — a larger curated set crowds harder, so the threshold is expected
to fall.

### 16.3 What this does to §15.2

§15.2's "a raw consumed without contributing is indistinguishable from one that was folded"
was filed as **`G18`** (PR #32). §16.2 is its measured **cause**: of the ten uncited raws,
roughly **six were suppressed by crowding and four are genuinely thin** — not ten
unknowables. **G18 stays `open`**: the fix shrinks the number without making contribution
observable, which was the actual complaint.

### 16.4 §15.4 stands

Nothing here touches the `fallback` correction — it remains the ordinary path for a store
dominated by Codex captures and authored v1 raws.

### 16.5 Method note, second iteration

§15's own method note recorded that *regenerating* the numbers rather than harvesting an
earlier session's summary killed two claims that had been recorded as settled. §16 adds the
next turn of the same screw: **§15.3 named its falsifier precisely and correctly, and naming
it was not the same as running it.** The unrun experiment sat one paragraph away from a
conclusion it would have inverted, described accurately enough that a reader could mistake
the description for the test.
