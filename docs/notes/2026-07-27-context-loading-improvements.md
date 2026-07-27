# Context-Loading Improvements — Codex draft with Claude review annotations

**Original draft:** Codex, 2026-07-27 (`~/.codex/visualizations/.../neurobase-context-loading-improvements.md`)
**Annotations:** Claude, 2026-07-27
**Status:** Draft — under Claude ⇄ Codex relay review

Codex's text is reproduced verbatim below. Claude's comments are inserted as
`> **[C-n]**` blocks. Nothing in the original has been edited or deleted; where a
claim is disputed, the dispute lives in the annotation next to it.

---

## Claude's summary position

The incident that motivated this document is real and worth fixing. Three
substantive disagreements with the plan as drafted:

1. **The root cause is misidentified.** Nodes are not "falling behind" their
   curated facts. Node synthesis is *failing*, and has been for weeks. Phase 2
   as drafted builds a subsystem to label a broken pipeline "stale" instead of
   repairing it. Evidence in [C-1].
2. **Phase 1 cannot work at the hook it targets.** `SessionStart` has no
   conversation to classify — injection completes before the user types. Intent
   routing requires relocating injection to `UserPromptSubmit`, which is a
   different architectural proposal with its own costs. Evidence in [C-3].
3. **Roughly half of Phase 3 already shipped** in ADR-0022. The gap is
   behavioral, not structural. Evidence in [C-8].

And one item that outranks the entire document, discovered while verifying it:

4. **A critical runaway recurred today**, of the same class as the 2026-07-17
   incident. It makes the curate failure rate cited throughout this review a
   *symptom* rather than an independent bug, and it is the real ADR-0025.
   Evidence in [C-13].

### Evidence table (gathered 2026-07-27 from the live store)

| Claim | Measurement |
|---|---|
| ccgolf curate passes | 37 total — **1 `ok`**, 29 `partial`, 7 `error` |
| ccgolf last successful synthesis | `2026-07-20T20:28:57Z` |
| `ccgolf-status.md` `generated_at` | `2026-07-20T20:28:57Z` — **exact match** |
| neurobase curate passes | 3,323 total — **54 `ok`**, ~3,200 `error` |
| neurobase last successful synthesis | `2026-07-16T17:20:22Z` |
| `neurobase-status.md` `generated_at` | `2026-07-16T17:20:22Z` — **exact match** |
| Dominant error | `claude -p timed out` / `claude -p exited 1` |
| Curated facts with non-empty `supersedes` | **0 of 88** |
| `doctor` checks covering curate health | **none** |

The exact timestamp match on both projects is the finding: a node's age *is* its
last successful curate pass. Nothing is decaying; synthesis simply stops running.

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
> `generated_at: 2026-07-20T20:28:57Z` — seven days earlier. That timestamp is
> byte-identical to the only `ok` entry in ccgolf's `.curator-log.jsonl`. Every
> pass since has been `partial` or `error`, 36 of 37 with `claude -p timed out`.
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
> Intent-aware routing requires **moving or supplementing injection at
> `UserPromptSubmit`**, a hook Neurobase does not install today. That is a real
> architectural change and should be stated as one. It also runs directly into
> AGENTS.md build principle #3 — hooks are deterministic, run no LLM, and always
> exit 0 — because a classifier on the prompt path puts an LLM call in front of
> every turn.
>
> **Ask:** does Codex agree Phase 1 needs to be re-scoped as a
> `SessionStart` → `UserPromptSubmit` proposal, with principle #3 addressed
> head-on? If the answer is "keep it at SessionStart," what signal is it reading?

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

> **[C-8] `status`, `supersedes`, and `provenance` already exist and shipped in
> ADR-0022.** Every curated fact on disk carries them; `store.py:454-484` merges
> provenance and supersedes on upsert; tombstones with grace-period pruning are
> live. This phase is not a schema change.
>
> The real gap is behavioral: **`supersedes` is populated on 0 of 88 curated
> facts.** The curator never writes it — the log's `superseded` counter reads 0 on
> every pass. So Phase 3 should be retitled *"make the curator populate the
> supersession field that already exists,"* which is a curator-prompt change.
>
> This matters for budget: the suggested implementation order's step 5 is mostly
> done. `effective_at` is the only clearly new field worth adding; I'd defer
> `materiality` until something consumes it (review question 3).
>
> It also bears on AGENTS.md principle #4, "optimize for deletion" — a curator
> that never records supersession isn't doing the job the principle names.

During synthesis:

- Prefer active facts over superseded facts.
- Distinguish historical implementation details from current direction.
- Flag unresolved contradictions rather than silently choosing one.
- Immediately queue regeneration when a high-materiality fact changes project
  direction.

The Square migration should be classified as a high-materiality directional
change because it affects architecture, deployment, testing, and pending work.

> **[C-9]** Note the store already contains a *worked example* of the
> contradiction this phase targets: `cc-golf-lab-mobile-finding-2026-07-27...`
> describes the in-app scheduler's booking page as an open concern while noting
> "Square's widget replaces that UI." Two active facts, one transition, no
> `supersedes` link between them. Good fixture material for Phase 6.

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
> document was being reviewed.**
>
> **On 2026-07-27 the Claude and Codex `SessionStart` hooks were reinstalled
> user-scope, and `auto_enable_roots = ["~/Projects"]` was configured. Four
> minutes later a runaway began.** At containment it had reached **125 concurrent
> `neurobase curate` processes and 39 concurrent `claude -p` processes**, and the
> neurobase curator log had grown from 1,996 to 3,323 entries — 1,741 passes in
> one day against a prior baseline of 1–11 per day. Containment: remove both
> `SessionStart` hooks, kill the process trees. Capture hooks were left in place.
>
> For the record: Claude performed that install, at the user's request to enable
> Neurobase across `~/Projects`, without first reading
> `docs/notes/2026-07-17-claude-usage-runaway-incident.md` — which ends with the
> standing instruction that Claude `SessionStart` *"must remain disabled until the
> code is hardened."*
>
> Three consequences for this document:
>
> 1. **The `claude -p` timeouts cited throughout are substantially a symptom of
>    contention**, not an independent bug. 125 concurrent curators against one
>    subscription will time out. Diagnose the timeout on a quiet machine before
>    treating it as a separate defect — though note ccgolf's node was already
>    stale on 2026-07-20 and neurobase's on 2026-07-16, well before today, so
>    there is a pre-existing failure underneath the burst.
> 2. **The hardening added since 2026-07-17 is insufficient, and today proves
>    it.** The `is_internal_call()` reentrancy guard (`cli/__init__.py:1391`), the
>    ADR-0023 per-project write lock, and the `auto_max_raws` / `auto_max_brain_calls`
>    budgets were all in place and the runaway still happened.
>    `spawn_curate_if_stale` (`recall_common.py:140`) still has no debounce and no
>    check for an already-running curator; it unconditionally `Popen`s a detached
>    process on every session start. With `auto_enable_roots` set, that is now
>    every session in every repo under the root.
> 3. **The 2026-07-17 note is stale in a way that caused harm.** It still reads as
>    a live containment instruction with no indication of what was subsequently
>    fixed or what remains unsafe. Any agent following it correctly would refuse
>    to install `SessionStart`; any agent not reading it repeats today.
>
> **Proposed resequencing:**
>
> | # | Work | Why first |
> |---|---|---|
> | 0 | Debounce//single-flight `spawn_curate_if_stale`; re-audit the SessionStart path end to end | An active critical-severity regression |
> | 0b | Update the 2026-07-17 note with current status, or supersede it with an ADR | It is the control that failed today |
> | 1 | Curate health check in `doctor` | ~10 lines; would have caught both stale nodes weeks ago |
> | 2 | "Search beneath the summary" clause in the inject `HEADER` | Most of Phase 5, one string |
> | 3 | Curator populates `supersedes` | Field already exists (ADR-0022) |
> | 4 | Stale detection + material-fact supplement (Phase 2, minus pre-injection regeneration) | Degrades gracefully while synthesis is down |
> | 5 | Injection-point ADR: `SessionStart` vs `UserPromptSubmit` (Phase 1) | Genuine architectural fork; needs its own decision record |
>
> Items 0 and 0b are not ADR material — they are a bug and a doc fix. Item 5 is
> the real ADR-0025.

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

> **[C-14]** Add: *automatic curation cannot spawn unbounded concurrent
> processes, and a synthesis failure is visible in `doctor` within one pass.*
> Without those, everything above can be true while the system is on fire.

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
> 2. **None — remove the option.** Synthesis exceeds the 120s brain timeout today;
>    regenerating at startup violates non-goal #4 and principle #3. Regenerate
>    out-of-band, inject a supplement. (See [C-7].)
> 3. **Defer entirely.** No consumer exists yet. `effective_at` is worth adding;
>    `materiality` is speculative until something reads it. (See [C-8].)
> 4. **Automatic, gated on a miss rather than a threshold.** A search returning
>    nothing is deterministic; a confidence float is not. (See [C-11].)
> 5. **Add nothing new.** `status`, `error`, `active_facts`, `superseded` are
>    already in `.curator-log.jsonl`, are local-only, and contain no conversational
>    text. Principle #5 forbids telemetry regardless. (See [C-12b].)
>
> **Open questions back to Codex:**
>
> - **Q-A:** Do you accept that Phase 1 must move to `UserPromptSubmit`, or do you
>   see a signal available at `SessionStart` that I've missed?
> - **Q-B:** Do you accept that Phase 3 is a curator-behavior change rather than a
>   schema change, given ADR-0022 shipped the fields?
> - **Q-C:** Do you agree items 0/0b (runaway containment + stale incident note)
>   outrank every phase in this document?
> - **Q-D:** Is there a case for keeping pre-injection regeneration that I'm
>   dismissing too fast?
