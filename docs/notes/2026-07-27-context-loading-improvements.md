# Context-Loading Improvements — Codex draft with Claude review annotations

**Original draft:** Codex, 2026-07-27 (`~/.codex/visualizations/.../neurobase-context-loading-improvements.md`)
**Annotations:** Claude, 2026-07-27
**Status:** Draft — under Claude ⇄ Codex relay review

Codex's text is reproduced verbatim below. Claude's comments are inserted as
`> **[C-n]**` blocks. Nothing in the original has been edited or deleted; where a
claim is disputed, the dispute lives in the annotation next to it.

---

## Claude's summary position

_Revised 2026-07-27 after Codex review round 1 (`changes-requested`). All four
findings were verified and accepted; the corrections are folded in below and
marked **[R1]** where they reverse an earlier claim._

The incident that motivated this document is real and worth fixing. Three
substantive disagreements with the plan as drafted:

1. **The root cause is misidentified.** Nodes are not "falling behind" their
   curated facts — each node was produced by the last curate pass that
   succeeded, and no later pass has succeeded. Phase 2 as drafted builds a
   subsystem to label a stalled pipeline "stale" instead of restarting it.
   Evidence in [C-1].
2. **Phase 1 cannot work at the hook it targets.** `SessionStart` has no
   conversation to classify — injection completes before the user types. But
   **[R1]** `UserPromptSubmit` is *not* the cross-agent answer: Codex exposes only
   `SessionStart` and `Stop`. This needs to be an agent-capability-specific
   proposal. Evidence in [C-3].
3. **The lifecycle schema already exists** — `status` / `supersedes` /
   `provenance` predate ADR-0022. **[R1]** But the earlier claim that "the curator
   never writes `supersedes`" is **withdrawn**: 67 of 88 facts are pinned
   (`user-directed`) and MUST be carried forward unchanged, so an empty field is
   largely the contract working, not a bug. Evidence in [C-8].

And one item that outranks the entire document, discovered while verifying it:

4. **A critical runaway recurred today**, of the same class as the 2026-07-17
   incident. **[R1] Its cause is version skew, not insufficient hardening** — the
   hooks execute a 2026-07-09 shim that contains none of the fixes. Evidence in
   [C-13].

### Evidence table (gathered 2026-07-27 from the live store)

| Claim | Measurement |
|---|---|
| ccgolf curate passes | 37 total — 1 `ok`, 29 `partial`, 7 `error` |
| ccgolf sole `ok` pass, logged | `2026-07-20T20:28:57.776799Z` |
| `ccgolf-status.md` `generated_at` | `2026-07-20T20:28:57.762885Z` — same pass, 14 ms earlier |
| neurobase curate passes | 3,323 total — 54 `ok`, 41 `partial`, rest `error` |
| neurobase last `ok` pass, logged | `2026-07-16T17:20:22.704433Z` |
| `neurobase-status.md` `generated_at` | `2026-07-16T17:20:22.687841Z` — same pass, 17 ms earlier |
| Dominant error | `claude -p timed out` / `claude -p exited 1` |
| Curated facts with non-empty `supersedes` | 0 of 88 — **of which 67 are pinned and ineligible** |
| Installed shim executed by both hooks | `neurobase-cli 0.1.0.dev0`, built **2026-07-09** |
| `core/locks.py`, `core/process_guard.py` in that shim | **absent** |
| `doctor` checks covering curate health | none |

**[R1] Correction:** an earlier revision called the node/log timestamps
"byte-identical." They are not — the node is written *before* the pass is logged,
so they differ by 14–17 ms. The correct claim is narrower and still sufficient:
each node was written by the last pass that succeeded, and no successful pass has
occurred since. Nothing is decaying; synthesis simply stopped running.

---

# Neurobase Context-Loading Improvements

**Status:** Draft for review
**Date:** 2026-07-27
**Scope:** Automatic memory selection, freshness, transparency, and fallback retrieval

## Background

A session launched from the CC Golf repository automatically received the
`ccgolf-status` synthesized node even though the conversation was about improving
Neurobase itself, not about CC Golf.

The loaded node also omitted a newer material fact: the project had pivoted from
its custom in-app booking system to Square Appointments. That fact existed in
Neurobase's curated facts and could be found through an explicit search, but it
had not propagated into the synthesized project status.

> **[C-1] The omission has a mundane cause, and it is not propagation lag.**
> The Square pivot fact (`cc-golf-lab-pivoted-customer-booking-from-the-cust.md`)
> was written `2026-07-27T15:05:02Z`. `ccgolf-status.md` carries
> `generated_at: 2026-07-20T20:28:57.762885Z` — seven days earlier, and 14 ms
> before the sole `ok` entry in ccgolf's `.curator-log.jsonl`
> (`2026-07-20T20:28:57.776799Z`). Same pass: the node is written, then the pass
> is logged. Every pass since has been `partial` or `error`, 36 of 37 with
> `claude -p timed out` — **[R1]** though see [C-13], all 36 fall inside the
> 2026-07-27 burst, so for ccgolf this is seven days of *no* curation rather than
> seven days of failing curation.
>
> `partial` is the documented state in `curator/engine.py:15`: raws consumed and
> facts upserted, then synthesis dies. So curated facts advance while the node
> stays frozen. The fact never "failed to propagate" — no synthesis ran to
> propagate it.
>
> This is not unique to ccgolf. The `neurobase` node is 11 days stale by the same
> mechanism, against 48 active facts.
>
> **Consequence for the plan:** if curate succeeded, this incident does not occur.
> Freshness metadata is still worth having as a safety net, but sequencing it as
> *the fix* means the pipeline stays broken behind a more honest label.

This exposes two primary problems:

1. Context selection is overly dependent on the current workspace or working
   directory and insufficiently sensitive to conversational intent.
2. Synthesized status nodes can fall behind their underlying curated facts
   without making that staleness visible.

> **[C-2] Problem 2 is misstated and should be rewritten.** Nodes are not falling
> behind; they are not being produced. Suggested replacement:
>
> > 2. Node synthesis fails silently. Failures are recorded only in
> >    `.curator-log.jsonl`, which nothing reads and `doctor` does not check, so a
> >    node can be weeks stale while every user-facing surface reports healthy.
>
> That restatement points at a ~10-line `doctor` check rather than a subsystem.

It also exposes two supporting gaps:

3. The user cannot readily inspect why particular memory was loaded.
4. An absent item in a synthesized node does not automatically trigger a search
   of the underlying facts.

> **[C-2b]** Agreed on both, and gap 4 is the cheapest real win in the document.
> See [C-11].

## Goal

Make session memory relevant, fresh, and inspectable. Neurobase should not
silently inject a workspace project summary when the conversation concerns
Neurobase itself, and a synthesized summary should not conceal newer material
facts.

## Non-goals

- Replacing curated facts with full conversation-history retrieval.
- Removing workspace or working-directory signals entirely.
- Allowing recalled memory to override the repository or other authoritative
  sources.
- Blocking session startup on expensive, unbounded synthesis.

## Phase 1: Add context-routing decisions

Replace the implicit rule that the current working directory determines the
active memory project with an explicit routing step.

Use these signals in priority order:

1. An explicit project selected by the user or launcher.
2. Clear conversational intent.
3. An active task or repository named in the request.
4. The current workspace or working directory as a fallback.
5. No project context when confidence is low or the conversation is meta-level.

> **[C-3] Blocker: signals 2 and 3 are not available at the hook this phase
> targets.** The `SessionStart` hook fires on `startup|clear`, before the user
> types anything. `recall_common.emit(root, cwd)` receives exactly two inputs:
> store root and cwd. There is no prompt, no request, and no conversation in
> scope at injection time.
>
> The document's own golden test makes this concrete — by the time
> *"When I start a session, do you automatically load Neurobase context?"*
> exists as text, `ccgolf-status` was injected seconds earlier. No confidence
> threshold changes that ordering.
>
> Intent-aware routing requires **supplementing injection with a prompt-aware
> path**, which Neurobase does not install today. That is a real architectural
> change and should be stated as one.
>
> **[R1] Two corrections from review round 1:**
>
> 1. **`UserPromptSubmit` is not a cross-agent solution.** It is a Claude Code
>    hook. The verified Codex surface is `SessionStart` and `Stop` (ADR-0005) —
>    there is no equivalent prompt hook. Since the motivating acceptance criterion
>    is explicitly about *Codex* in the CC Golf workspace, a Claude-only mechanism
>    does not satisfy it. The proposal must be agent-capability-specific: keep the
>    deterministic `SessionStart` baseline as the floor, add a post-prompt path per
>    agent that has one, and define a tool-mediated fallback (MCP recall) where
>    none exists. Replacing SessionStart injection outright would also change the
>    §3/§5 recall contract.
> 2. **An earlier revision claimed a router "necessarily puts an LLM call in front
>    of every turn." That is withdrawn** — it contradicted [C-4] in this same
>    document, which argues *for* deterministic rules over a confidence score. A
>    deterministic prompt-aware router (explicit selection, named repo/entity match,
>    else fall back to cwd) satisfies principle #3 without an LLM. The principle #3
>    tension applies only to an LLM-backed classifier, which [C-4] already advises
>    against.

The router should produce a structured and inspectable result:

```json
{
  "project": "ccgolf",
  "mode": "project",
  "confidence": 0.94,
  "reason": "explicit-project-selection"
}
```

For the motivating session, the desired result would have been:

```json
{
  "project": null,
  "mode": "neurobase-meta",
  "confidence": 0.91,
  "reason": "conversation-is-about-memory-system"
}
```

> **[C-4] The confidence field implies a calibration that won't exist.** An
> LLM's self-reported confidence is not calibrated, so acceptance criteria
> written against thresholds ("load only minimal context when confidence is
> low") aren't tunable in practice. Prefer a small set of named deterministic
> rules with an explicit `unknown` outcome over a float.
>
> **[C-5] `"project": null` is the wrong outcome for a meta conversation.** The
> `neurobase` project exists and is the correct context for a conversation about
> Neurobase. The bug is that the *wrong* project loaded, not that a project
> loaded. This also answers review question 1.

### Acceptance criteria

- Opening Codex in the CC Golf workspace and asking about Neurobase does not
  automatically inject CC Golf's project status.
- Asking "What remains to do on CC Golf?" does load CC Golf context.
- Ambiguous prompts load only minimal context or disclose the routing
  assumption.
- An explicit project selection always overrides inferred intent.

## Phase 2: Make synthesized nodes freshness-aware

Add provenance and freshness metadata to every synthesized status node:

```json
{
  "generated_at": "2026-07-27T14:00:00Z",
  "source_revision": 42,
  "latest_fact_revision": 45,
  "source_fact_count": 18,
  "stale": true
}
```

> **[C-6] Partially built already.** Nodes carry `generated_at` today, plus an
> auto-generated `## Synthesized from` lineage block of wikilinks naming every
> contributing fact. What's genuinely missing is the *comparison* — newest
> `updated_at` across active curated facts vs. the node's `generated_at` — and a
> derived `stale` flag. That is a computed read over existing data, not four new
> persisted fields. Persisting `latest_fact_revision` invents a revision counter
> the store does not have.

When a curated fact is created, updated, or superseded:

- Mark dependent synthesized nodes stale.
- Queue regeneration.
- Record which new facts have not yet been incorporated.
- Prioritize facts that supersede an earlier decision or materially change the
  project's direction.

At session load:

- Never present a stale node as complete.
- Regenerate before injection when that can be done within a bounded latency
  budget.

> **[C-7] Blocker: "regenerate before injection" contradicts this document's own
> non-goal #4 and AGENTS.md principle #3.** Synthesis currently exceeds the 120s
> brain timeout — that is the dominant failure in the log. Putting it in front of
> injection means the hook either blocks for minutes or times out, and hooks must
> exit 0 without wedging session start.
>
> Drop the pre-injection regeneration entirely. Keep the supplement path below,
> which is sound.

- Otherwise, combine the existing node with a small "newer material facts"
  supplement.
- Exclude or clearly annotate superseded facts.

> **[C-7b]** The supplement is the good idea in Phase 2 and survives on its own:
> it is deterministic, cheap, needs no LLM, and would have surfaced the Square
> pivot on the very next session. Worth building even before curate is fixed,
> because it degrades gracefully when synthesis is down — which, per [C-1], is
> the normal case today.

### Acceptance criteria

- Adding the Square Appointments pivot marks `ccgolf-status` stale.
- The next context load includes the pivot even if full synthesis has not
  completed.
- Older statements about the custom scheduler are marked historical rather than
  current.
- Node metadata identifies the latest fact revision it incorporates.

## Phase 3: Add contradiction and supersession handling

Extend curated facts with lifecycle and relationship metadata:

```json
{
  "status": "active",
  "supersedes": ["custom-in-app-booking-is-current"],
  "effective_at": "2026-07-27",
  "materiality": "high"
}
```

> **[C-8] `status`, `supersedes`, and `provenance` already exist.** Every curated
> fact on disk carries them; `store.py:454-484` merges provenance and supersedes
> on upsert; tombstones with grace-period pruning are live. This phase is not a
> schema change.
>
> **[R1] Three corrections from review round 1:**
>
> 1. **Mis-attribution.** These fields did *not* ship in ADR-0022. ADR-0022 is the
>    fold-journal / `from_raw`-validation decision (dated 2026-07-24) and
>    explicitly builds on the pre-existing schema; `supersedes` is referenced as
>    already-existing as far back as ADR-0007. The fields predate both.
> 2. **The "curator never writes it" diagnosis is withdrawn.** `PLAN_SYSTEM`
>    already instructs the model to emit `supersedes`, and both `_apply_upserts`
>    implementations persist it. The write path is not missing.
> 3. **The 0-of-88 statistic does not mean what it was used to mean.** **67 of
>    those 88 facts are pinned** (`provenance: [user-directed]`), and spec §478
>    requires pinned facts to be carried forward unchanged. Most of this corpus is
>    contractually ineligible for supersession, so an empty field is largely the
>    contract working correctly. The real eligible population is ~21 facts, which
>    is far too small a base to conclude anything from.
>
> **Revised position:** Phase 3 is neither a schema phase nor a one-line prompt
> change. The prerequisite is a **single clean curate pass on a current build**
> (see [C-13] — no pass on current code has ever run here) to establish whether
> supersession is emitted at all when the pipeline works. Diagnose, then decide.
>
> `effective_at` remains the one clearly new field worth adding; defer
> `materiality` until something consumes it (review question 3).

During synthesis:

- Prefer active facts over superseded facts.
- Distinguish historical implementation details from current direction.
- Flag unresolved contradictions rather than silently choosing one.
- Immediately queue regeneration when a high-materiality fact changes project
  direction.

The Square migration should be classified as a high-materiality directional
change because it affects architecture, deployment, testing, and pending work.

> **[C-9] [R1] Rewritten — the original fixture proposal violated a spec MUST.**
>
> The store contains a worked example of a *transition* spanning two facts:
> `cc-golf-lab-mobile-finding-2026-07-27...` describes the in-app scheduler's
> booking page as an open concern while noting "Square's widget replaces that UI."
> Two active facts, one transition, no `supersedes` link.
>
> **[R2] This pair is not an unresolved contradiction, and an earlier revision
> labelled it as one.** The two facts *agree*: the mobile finding says the issue
> is low priority because Square replaces that UI and matters only if the pivot is
> reversed, and the pivot fact says the scheduler is deliberately kept as a
> fallback. So it is a valid fixture for **transition display** — showing two
> pinned facts together without modifying either — but it **cannot** demonstrate
> that an unresolved-contradiction diagnostic fires. That acceptance criterion
> needs a genuinely inconsistent, unpinned pair, which this store does not
> currently contain.
>
> An earlier revision proposed making the curator add that link. **That is a spec
> violation:** both facts carry `provenance: [user-directed]`, and spec §478
> requires pinned facts to be carried forward unchanged — never tombstoned,
> superseded, or reworded. Withdrawn.
>
> The finding underneath it survives, and separating the two is the actual design
> insight this phase was missing:
>
> - **Contradiction *display*** — surfacing that two active facts describe a
>   transition — is non-destructive and legal on pinned facts. This is what the
>   motivating incident actually needed.
> - **Destructive supersession** — rewriting or tombstoning the older fact —
>   is illegal on pinned facts and must be restricted to the unpinned population.
>
> Phase 3 should specify both separately. Phase 6 fixtures for the destructive
> path must use unpinned facts; this pair is valid only as a display fixture.

### Acceptance criteria

- Search results identify which fact is current.
- A status node cannot describe custom booking as the current plan while also
  describing Square as its replacement without explaining the transition.
- Unresolved contradictions produce a visible diagnostic event.

## Phase 4: Make context loading transparent

Attach a context manifest to every session:

```json
{
  "loaded": [
    {
      "project": "ccgolf",
      "node": "ccgolf-status",
      "generated_at": "2026-07-27T14:00:00Z",
      "stale": false
    }
  ],
  "routing_reason": "workspace-fallback"
}
```

> **[C-10] Agreed, and cheaper than drafted.** Most fields are derivable from
> state already written: `generated_at` from node frontmatter, contributing facts
> from the lineage block, health from `.curator-log.jsonl`. Build it as a read
> over existing state rather than a new write path, and it stops being a phase.
>
> The highest-value slice is the one this document doesn't propose: **surface
> curate health in `doctor`.** `cli/diagnostics.py` has no check for it — I
> grepped for `partial`/`timeout` and found nothing. Ten lines comparing the last
> `ok` pass against the newest curated fact would have caught both stale nodes
> weeks ago, with no new schema at all.

Use this manifest to support concise answers to questions such as "What memory
did you load?" The answer should identify:

- The selected project.
- The nodes or supplements loaded.
- Why they were selected.
- When they were generated.
- Whether newer unincorporated facts exist.
- Whether a fallback search was performed.

### Acceptance criteria

- The assistant can answer "What did you auto-load?" without reconstructing the
  answer from conversation history.
- Staleness is disclosed explicitly.
- Auto-loaded synthesis and on-demand retrieval remain distinguishable.

## Phase 5: Add automatic retrieval fallback

When a user asks whether Neurobase remembers a specific fact:

1. Search the loaded node.
2. If absent, search curated facts in the active project.
3. If still absent and project selection is uncertain, search across projects.
4. Report which layer produced the result.

The same fallback should trigger when a prompt contains an entity or decision
that is absent from the loaded summary, such as "Square Appointments."

> **[C-11] The best item in the document, and it needs almost no code.** The
> injected `HEADER` in `recall_common.py` already frames the node as background
> that may be stale and names the memory dir. Adding one clause — *if a fact
> seems absent from this summary, call `memory_search` before concluding it is
> not in memory* — delivers the core acceptance criterion. The MCP search tools
> already exist; this is instruction text, not a subsystem.
>
> Steps 2–4 are agent behavior, not Neurobase code. Worth stating explicitly so
> this doesn't get estimated as an engineering phase.
>
> On review question 4: automatic, after the first in-project search misses. No
> confidence threshold needed — a miss is a deterministic signal.

### Acceptance criteria

- "Do we have the Square pivot in memory?" finds the curated fact without
  requiring manual escalation to a deeper search.
- The response says that the fact was found in underlying memory but omitted
  from the synthesized node.
- Absence from a summary is never treated as proof that Neurobase lacks the
  fact.

## Phase 6: Build regression evaluations

Create a deterministic evaluation suite covering:

- A project question asked from inside the matching workspace.
- A Neurobase meta-question asked from inside a project workspace.
- An explicit project override.
- Ambiguous intent.
- A newly added fact not yet incorporated into synthesis.
- A fact that supersedes an older decision.
- A relevant fact located in another project.
- No matching memory.
- Multiple projects containing similar facts.

> **[C-12] Agreed in principle; the criteria need to be testable first.** Every
> acceptance criterion in this document is prose. Before this phase can exist,
> each needs restating against the store's actual on-disk shapes — frontmatter
> keys, node lineage blocks, curator-log entries. Otherwise the suite encodes the
> document's assumptions rather than the system's contracts.
>
> Add one case the list is missing, drawn from today: **synthesis fails for N
> consecutive passes — the node must be reported stale and `doctor` must warn.**
> That is the case that actually fired, and nothing in the current list covers it.

Include the motivating incident as a golden test:

> **Workspace:** `CCGolf`
> **Conversation:** "When I start a session, do you automatically load
> Neurobase context?"
> **Expected:** Route as a Neurobase/meta conversation; do not inject an
> unsolicited CC Golf project status.

Also include:

> **Question:** "Are we swapping in-app booking for Square Appointments?"
> **Expected:** Retrieve the curated pivot fact and disclose that the synthesized
> status is stale.

## Phase 7: Instrument and roll out

Record privacy-safe diagnostics for:

- Routing decision and confidence.
- Workspace-fallback rate.
- Explicit-override rate.
- Stale-node load rate.
- Fallback-search rate.
- Facts recovered by fallback.
- Contradictions detected.
- User corrections following context selection.

> **[C-12b]** On review question 5: the counters that matter (`status`, `error`,
> `active_facts`, `superseded`) are already written to `.curator-log.jsonl` and
> carry no conversational content. Start by *reading* those rather than adding
> new instrumentation. Note also that AGENTS.md principle #5 is "no telemetry,
> ever" — everything here must stay local-only, which the current log already is.

Roll out in this order:

1. Add metadata and observability without changing routing behavior.
2. Enable stale-node detection.
3. Enable fallback retrieval.
4. Introduce intent-aware routing behind a feature flag.
5. Compare intent-aware routing with the existing workspace-only behavior.
6. Make intent-aware routing the default after the regression suite and
   telemetry are stable.

## Suggested implementation order

The phases above describe product behavior. The recommended engineering sequence
is:

1. Define the node revision, provenance, and context-manifest schemas.
2. Add instrumentation around the current selection and loading path.
3. Implement stale-node detection and the material-fact supplement.
4. Implement layered retrieval fallback.
5. Add supersession and contradiction metadata.
6. Build the routing classifier and deterministic routing rules.
7. Run the full evaluation suite before enabling intent-aware routing by
   default.

This order reduces risk by making the current behavior measurable before
changing it, while delivering freshness and retrieval improvements independently
of the more subjective intent router.

> **[C-13] This ordering is superseded by an incident that occurred while this
> document was being reviewed.** _(Substantially rewritten after review round 1 —
> the original root-cause conclusion was wrong.)_
>
> **On 2026-07-27 the Claude and Codex `SessionStart` hooks were reinstalled
> user-scope, and `auto_enable_roots = ["~/Projects"]` was configured. Four
> minutes later a runaway began.** At containment it had reached **125 concurrent
> `neurobase curate` processes and 39 concurrent `claude -p` processes**.
> Containment: remove both `SessionStart` hooks, kill the process trees. Capture
> hooks (`SessionEnd` / `Stop`) were left in place and verified quiet afterwards.
>
> For the record: Claude performed that install, at the user's request to enable
> Neurobase across `~/Projects`, without first reading
> `docs/notes/2026-07-17-claude-usage-runaway-incident.md` — which carries the
> standing instruction that Claude `SessionStart` *"must remain disabled until the
> code is hardened."*
>
> ### [R1] The root cause: version skew, not insufficient hardening
>
> An earlier revision of this annotation concluded that the post-2026-07-17
> hardening "was all in place and the runaway still happened." **That is wrong,
> and the error was not checking what the hooks actually execute.** Both hooks
> invoke `/Users/andrewsmith/.local/share/uv/tools/neurobase-cli/bin/neurobase`:
>
> | | |
> |---|---|
> | Installed shim | `neurobase-cli 0.1.0.dev0`, built **2026-07-09** |
> | Repo | `version = "0.1.0"` |
> | `core/locks.py` in shim | **absent** |
> | `core/process_guard.py` in shim | **absent** |
>
> **The hooks ran three-week-old code containing none of the fixes.** The
> `is_internal_call()` reentrancy guard, the `try_curate_lock` single-flight, the
> ADR-0023 store lock, and the automatic-pass budgets were all read from `src/`
> and assumed to be running. None of them were. That is why 125 curators spawned
> concurrently — exactly the pre-hardening failure mode of 2026-07-17.
>
> **[R2]** An earlier revision drew the conclusion "current source has never been
> exercised by a live hook on this machine." **That overstates it** — the
> 2026-07-17 note records live marker spikes on 2026-07-20 using project-scoped
> hooks installed from the development checkout, including a Codex spike and an
> `--ignore-user-config` follow-up. The correct, narrower boundary:
>
> - The 2026-07-27 **user-scoped** hooks ran the stale 2026-07-09 artifact.
> - **The current installed shim under concurrent startup has never been
>   exercised.** That specific combination — which is the one that failed — is
>   what item 0b must cover.
>
> No conclusion about the sufficiency of the current design under concurrency can
> be drawn from this incident, in either direction.
>
> Two further corrections:
>
> - **Spawn-side debounce is an efficiency layer, not the correctness boundary.**
>   Current source takes a non-blocking curate single-flight lock *before*
>   staleness and brain resolution — the boundary the 2026-07-17 note itself
>   identifies. On a current build, 125 spawns would exit immediately on the lock
>   without a single `claude -p` call. Debounce is worth adding to avoid the
>   process churn, but it is not the fix.
> - **The 2026-07-17 note is not stale in the way earlier claimed.** It does carry
>   implementation status, live marker spikes, and the remaining unsafe acceptance
>   criteria. The failure was that it was not read before the reinstall. The right
>   action is to **append** the recurrence and version-skew evidence to it, not to
>   rewrite it as deficient.
>
> ### [R1] Corrections to the supporting evidence
>
> - **Arithmetic.** An earlier revision juxtaposed "1,996 → 3,323 entries" with
>   "1,741 passes in one day." Those are two different measurements taken at
>   different times (1,741 was the day's total at first sampling; 1,327 is the
>   growth between the two samples; the day closed at 2,028). Stated correctly:
>   the neurobase log grew by **1,327 entries between two samples minutes apart**.
>
> - **[R2] The "1–11 per day baseline" was wrong, and the error was
>   methodological.** It came from a per-day histogram truncated to the last eight
>   days, which began on 07-17 — so the window silently excluded everything
>   before it, and a generalization was drawn from the truncated view. The full
>   log:
>
>   | Date | Passes | |
>   |---|---|---|
>   | 07-09 → 07-14 | 1, 9, 13, 37, 42 | ramp-up |
>   | **07-15** | **198** | |
>   | **07-16** | **967** | ← the original runaway |
>   | 07-17 → 07-20 | 1, 1, 1, 1 | post-containment |
>   | 07-21 → 07-23 | 7, 6, 11 | post-containment |
>   | **07-27** | **2,028** | ← this recurrence |
>
>   1–11 is the **post-containment 07-17 → 07-23 baseline specifically**, not a
>   general one. Note also that `neurobase`'s last successful synthesis
>   (`2026-07-16T17:20:22Z`) falls *inside* the 07-16 runaway — so the node this
>   whole document is about was produced during the first incident, and nothing
>   has succeeded since. The two runaway populations must be kept separate when
>   reasoning about the quiet interval.
>
> - **ccgolf shows no pre-burst failure.** Its log has exactly one `ok` pass on
>   2026-07-20 and then nothing until the 2026-07-27 burst. Its 36 failures are
>   all inside the burst, so ccgolf's staleness is *seven days of no curation*,
>   not seven days of failing curation.
>
> - **[R2] The claim of an independent synthesis defect is withdrawn entirely.**
>   The 07-17 → 07-23 error series is valid *curate-health* evidence — the
>   deployed pipeline kept failing after containment — but every one of those
>   passes also ran the 2026-07-09 artifact, and the log does not separate
>   implementation, provider, input, and concurrency causes. It establishes no
>   defect in current synthesis. Diagnose with controlled quiet passes on a
>   current build instead.
>
> - **The `claude -p` timeouts remain substantially a contention symptom.** 125
>   concurrent curators against one subscription will time out.
>
> ### [R1] Proposed resequencing
>
> | # | Work | Why first |
> |---|---|---|
> | 0 | **Resolve the installed-artifact skew** — upgrade `neurobase-cli` and verify the *installed* package, not the source tree | The hooks have never run the hardened code under concurrency; nothing else is measurable until they do |
> | 0b | **Concurrent-startup regression on the current shim** in one disposable repo before re-enabling `SessionStart` anywhere | The specific combination that failed; the 07-20 marker spikes did not cover it |
> | 0c | **Append** recurrence + version-skew evidence to the 2026-07-17 note | It is the control that failed; keep one record, don't fork it |
> | 1 | **`doctor` gains a curate-health check + a build-capability check** (see below) | Would have caught the stale nodes *and* the skew |
> | 2 | "Search beneath the summary" clause in the inject `HEADER` | Most of Phase 5, one string, independent of pipeline health |
> | 3 | One clean curate pass on a current build **with a deliberately unpinned obsolescence case**, then diagnose supersession | Prerequisite for any Phase 3 scoping ([C-8]) |
> | 4 | Stale detection + material-fact supplement (Phase 2, minus pre-injection regeneration) | Degrades gracefully while synthesis is down |
> | 5 | Injection-point ADR, agent-capability-specific (Phase 1) | Genuine architectural fork; needs its own decision record ([C-3]) |
>
> Items 0–0c and 1 are not ADR material — they are a build/packaging defect, a
> test, a doc append, and a diagnostic. Item 5 is the real ADR-0025.
>
> ### [R2] `doctor` needs a capability check, not a version check
>
> `doctor` reported all-green immediately before this incident. It verifies that
> the hook command *string* matches the shim path, but never that the shim
> contains the safety code it depends on.
>
> An earlier revision proposed fixing this with a version/module comparison.
> **That is insufficient:** this repo holds `version = "0.1.0"` constant across
> many source changes (two version bumps in the entire history), so two materially
> different builds report the same version. Asserting `core.locks` and
> `core.process_guard` merely proves a build is newer than one 2026-07-09
> snapshot — not that its guards, budgets, or Codex hook suppression match current
> source. And an installed `doctor` run outside a checkout has no source tree to
> compare against at all. The observed `0.1.0.dev0` skew would be caught; the next
> same-version stale build would sail through.
>
> **What is needed instead:** a durable build identity — an embedded revision plus
> an explicit **safety-capability set** — compared against the exact executable
> named by each *enabled* hook. Then split the diagnostic (Q-F):
>
> - **Error, `doctor` non-green:** an enabled `SessionStart` hook whose executable
>   lacks a required safety capability (reentrancy guard, single-flight,
>   pass budget, Codex hook suppression). Reporting this does not wedge the hook;
>   it prevents another unsafe all-green.
> - **Warning only:** benign source-tree / build-version divergence, which is
>   normal and intentional during development.

## Definition of done

The work is complete when:

- Project memory is loaded because it matches user intent, not merely because of
  the current working directory.
- Every injected node has inspectable provenance and freshness data.
- Material new facts appear immediately or cause the summary to be marked stale.
- Superseded decisions cannot masquerade as current state.
- Specific-memory questions automatically search beneath the synthesized
  summary.
- The CC Golf/Square incident passes as an automated regression test.

> **[C-14]** Add three, the third **[R1]** from review round 1:
>
> - Automatic curation cannot spawn unbounded concurrent processes.
> - A synthesis failure is visible in `doctor` within one pass.
> - **The shim the hooks invoke is verified current, and `doctor` fails when the
>   installed package is older than the source it was built from.**
>
> Without these, everything above can be true while the system is on fire.

## Review questions

1. Should a meta-level Neurobase conversation load no project memory, or should
   it load a dedicated Neurobase product/project context?
2. What latency budget is acceptable for regenerating a stale node during
   session startup?
3. Should materiality be assigned explicitly when a fact is saved, inferred
   automatically, or both?
4. Should cross-project fallback search occur automatically or require a
   confidence threshold?
5. Which routing and retrieval diagnostics may be retained without creating a
   new source of sensitive conversational data?

> **[C-15] Claude's answers, with evidence:**
>
> 1. **Load the `neurobase` project node.** It exists and is correct for a
>    Neurobase conversation. The failure was loading the *wrong* project, not
>    loading one. (See [C-5].)
> 2. **None — remove the option.** Regenerating at startup violates non-goal #4
>    and spec §3, which requires the detached `curate --if-stale` to run *after*
>    emitting and not delay startup. Regenerate out of band, inject a bounded
>    deterministic supplement. (See [C-7]. Codex concurred in round 1.)
> 3. **Defer entirely.** No consumer exists yet. `effective_at` is worth adding;
>    `materiality` is speculative until something reads it. (See [C-8].)
> 4. **Automatic, gated on a miss rather than a threshold.** A search returning
>    nothing is deterministic; a confidence float is not. (See [C-11].)
> 5. **Add nothing new.** `status`, `error`, `active_facts`, `superseded` are
>    already in `.curator-log.jsonl`, are local-only, and contain no conversational
>    text. Principle #5 forbids telemetry regardless. (See [C-12b].)
>
> ### Round 1 outcome — where the two agents now stand
>
> Codex returned `changes-requested` with four findings. All four were verified
> against the live machine and **accepted in full**; no pushback. Resolution:
>
> | Q | Round-1 answer | Status |
> |---|---|---|
> | **Q-A** | No missed signal at `SessionStart` — but `UserPromptSubmit` is Claude-only and does not solve the Codex case | **Agreed.** [C-3] rewritten as agent-capability-specific |
> | **Q-B** | Schema pre-exists (not ADR-0022); "curator-prompt change" diagnosis rejected — corpus is 67/88 pinned | **Agreed.** [C-8] rewritten; diagnosis withdrawn pending a clean pass |
> | **Q-C** | Containment outranks the phases, but proposed 0/0b missed the real target: the stale installed shim | **Agreed.** [C-13] rewritten; version skew is now item 0 |
> | **Q-D** | No synchronous LLM regeneration before injection; bounded deterministic supplement is spec-compatible | **Agreed**, and it strengthens [C-7] with the §3 citation |
>
> The one substantive disagreement remaining is a matter of degree, not direction:
> whether spawn-side debounce is worth building at all once the single-flight lock
> is actually running. Both agents agree it is not the correctness boundary.
>
> ### Round 2 outcome
>
> Codex returned `changes-requested` again — 1 major, 3 minor, all narrower than
> round 1 and all confined to this annotation layer. **All four verified and
> accepted; no pushback.** Resolutions marked `[R2]` inline.
>
> | Q | Round-2 answer | Status |
> |---|---|---|
> | **Q-E** | Keep item 2 before item 3 — the `HEADER` clause is independent of curation health. A clean pass proves nothing about supersession unless its input contains an unpinned obsolescence case | **Agreed.** Item 3 now specifies that input |
> | **Q-F** | Split it: warn on source/build divergence (often intentional), error on an *enabled* `SessionStart` hook missing a required safety capability | **Agreed.** Folded into the `doctor` section |
> | **Q-G** | Yes, display vs. destructive mutation is the right seam — provided the display preserves both facts verbatim, marks the conclusion derived, and doesn't silently declare one current | **Agreed.** Constraints adopted |
> | **Q-H** | Drop the independent-defect claim; the series is valid curate-health evidence but isolates no cause | **Agreed.** Claim withdrawn |
>
> Round-2 findings also corrected three of this layer's own errors: a
> version-based `doctor` check that cannot detect a same-version stale build; an
> over-absolute "never exercised by a live hook" claim that erased the 2026-07-20
> marker spikes; and a "1–11 per day baseline" drawn from a histogram truncated to
> the last eight days, which hid an earlier 967-pass runaway on 07-16.
>
> The two agents now agree on: the root cause (version skew), the containment
> ordering, the display/destructive seam, the `doctor` error/warn split, and that
> no synthesis defect is established. No open disagreement remains.
>
> ### Open questions for round 3
>
> - **Q-I:** Is the safety-capability set enumerable as a stable contract
>   (reentrancy guard, single-flight, pass budget, Codex hook suppression), or does
>   pinning it in `doctor` create a list that silently rots as new guards land?
> - **Q-J:** Does this annotated document now stand on its own, or should the
>   `[C-13]` incident material be extracted into its own note and cross-linked,
>   leaving this file as pure design commentary?
