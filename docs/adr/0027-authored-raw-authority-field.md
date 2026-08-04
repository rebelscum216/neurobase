# ADR-0027: `authority` on a raw — an agent-authored digest is a distinct capture class, on schema 1

- **Status:** Proposed
- **Date:** 2026-08-04
- **Resolves:** decision 6 ("ship the session slice schema-1-only") of
  [`docs/notes/2026-08-01-memory-layering-ideas.md`](../notes/2026-08-01-memory-layering-ideas.md)
  §9.4/§10.2/§11.4
- **Unblocks:** the `neurobase wrap` authored-digest verb in the
  [build plan](../neurobase-build-plan.md) §6 Backlog
- **Related:** [ADR-0014](0014-transcript-distill-curation.md) (D15–D17, the distill
  path this bypasses) · [ADR-0016](0016-store-schema-2-project-records-profiles.md)
  (schema 2, accepted-but-unimplemented) · [ADR-0023](0023-project-store-write-lock.md)
  (the lock any wrap-time write must take) · D13 (redaction) ·
  [`G10`](../known-gaps.md) (transcript expiry)

All line citations below are against `main@70be738` and were re-verified there; the
source note cites `main@3642bd9`, where several had different numbers.

## Context

Three findings from 2026-08-04, in the order they matter.

**1. Retroactive transcript distill is structurally cache-hostile, and it is now
measured.** A 22-call, 6-generation run across three sessions spent 154,283 output
tokens against **959,550 cache-creation tokens**, at a **0.44% cache hit rate** on the
first clean run. (A later 9.34% figure is an artifact of one retry re-reading its own
cached content and is not the figure of record.) That rate is not a tuning failure to
be improved: distill chunks a transcript no subsequent call will ever read again, so
every chunk is unique content — the cache is *written and never read*, and the write
premium is paid on roughly a million tokens per pass of this size. Per-capture distill
latency was separately measured at **1.8–2.3 min** across 27 captures, worst single
capture 3 m 32 s. The measurement is attributable only because PR #17 (`0b553fa`)
records tokens, cost and per-model call counts on the pass journal entry.

**2. The consumption path for an authored body already exists, and it costs nothing.**
`store.write_raw(..., transcript_path=None)` writes a **v1 raw**. `_distill_one` then
returns `_Outcome(None, "no_pointer", False)` at
[`curator/distill.py:478-481`](../../src/neurobase/curator/distill.py) — *before* any
fingerprint, cache read, transcript render or brain call. `distill_docs` appends that
document **unchanged** ([`:607-609`](../../src/neurobase/curator/distill.py)), so the
curator plans over the raw's own body. No distill call, no digest-cache entry, no
transcript read — and therefore nothing that can expire, which means a wrapped session
is not exposed to **`G10`** at all. One bound survives: an exceptionally large body can
still be trimmed by the plan-payload assembler at `plan_payload_max_bytes`
(`curator/engine.py:345,465`), so "the fold sees the body" is true up to that ceiling.

**3. But a body written that way is indistinguishable from a captured one.** Every raw
today carries exactly `agent`, `session_id`, `cwd`, `branch`, `captured_at`, `consumed`
— plus `transcript_path`/`capture_version` when a pointer was given
([`core/store.py:353-364`](../../src/neurobase/core/store.py)). Nothing records *how
the body came to exist*. Capture is deterministic by construction: a scribe renders
events that happened. An LLM-authored digest is a model's account of a session, which
is a different kind of object with different failure modes — it can be confidently
wrong in ways a render cannot. Writing both under one shape means the curator, the fold
journal, `doctor` and the UI all treat the two as the same evidence.

That third point is what this ADR decides. Finding 1 is the *reason to act*, and it is
worth stating plainly that **the cost argument stands independently of the open quality
question**. Whether an authored summary reads better than a distilled one is §10.3 of
the source note and remains unanswered — the stance test answered a narrower question
(successor-framing yields 13–22% shorter output at equal section coverage) and
explicitly could not isolate authorship. Authoring removes the per-capture distill cost
whether or not the prose is better. The two justifications do not both need to hold,
and this ADR rests only on the first.

## Decision

**D45 — a raw MAY carry an `authority` frontmatter key; its absence means `captured`.**
Values for now are `captured` and `agent-authored`. Readers MUST tolerate its absence,
exactly as they already must for `transcript_path`/`capture_version` — a requirement
`write_raw`'s own contract already states
([`core/store.py:325-328`](../../src/neurobase/core/store.py)). Absent is not the same
as `captured` being written explicitly, but they mean the same thing; no reader may
treat the distinction as significant.

**D46 — this lands on store schema 1. It does not bump `STORE_SCHEMA_VERSION`, and it
does not wait for schema 2.** The precedent is ADR-0014, which introduced
`capture_version: 2` as a **per-document** marker while `STORE_SCHEMA_VERSION` stayed
`1` ([`core/store.py:29`](../../src/neurobase/core/store.py)). The D11 guard refuses
only a store whose recorded `schema` *exceeds* the running binary's
([`:120-123`](../../src/neurobase/core/store.py)); an additive optional key on a
document never crosses it. Gating this on ADR-0016's schema 2 inverts the dependency:
schema 2's record/source identity would then be justified by a premise — that authored
digests are worth having — which nothing has yet tested. A migration of one optional
key is cheaper than an abstraction built before the feature is known to be worth it.

**D47 — `authority` is a claim about provenance, not a grant of trust.** It MUST NOT
raise (or lower) a document's weight in planning, ranking, recall, or proposal mining.
Its guaranteed consumers are only the surfaces that would otherwise *misdescribe* what
they hold: the fold journal, `doctor`, and any UI that names the rung it is showing. If
some later change wants authored bodies weighted differently, that is its own ADR with
its own evidence — this one deliberately gives the field no behavioral power, so that
adding the field cannot quietly become a policy change.

**D48 — the D13 redaction guarantee is unconditional on `authority`.** D13 is a
whole-raw guarantee that explicitly covers scribe-written frontmatter (`cwd`, `branch`)
and explicitly excludes `session_id` (spec appendix §D13). `store.write_raw` writes the
supplied body **as-is** — it has no redaction pass of its own — so any caller producing
an authored raw MUST apply the redaction table to the body and to the informational
frontmatter it supplies, before calling it. A path that reaches `write_raw` without
that pass is a D13 violation regardless of who authored the text; nothing about a body
being agent-authored makes it safe. In particular, a proof-of-concept that calls store
internals directly is a one-off harness, not a demonstration of a supported path.

## Consequences

- **The field is the cheap half. Ingestion is not, and this ADR does not build it.** A
  raw's identity is its `captured_at`, **not** its `session_id`: `raw_filename` derives
  the name from the `(captured_at, agent, session_id)` tuple at microsecond precision
  ([`core/store.py:200-211`](../../src/neurobase/core/store.py)), and its own comment
  scopes the session-keyed overwrite trick to *"a scribe reusing a **stable**
  `captured_at`"*. The Claude scribe reuses nothing — it passes a fresh
  `datetime.now(UTC)` on every capture
  ([`adapters/claude/scribe.py:347`](../../src/neurobase/adapters/claude/scribe.py)).
  So a naive wrap-time write **adds** a second raw for the session rather than replacing
  its skim, and the fold would see the session twice. Live evidence, previously misread
  as harmless redundancy: the `transactiontracker` project holds **four separate raws
  for one session** (`499ca1a4`) at four timestamps. Doing this correctly requires
  discovering the session's existing unconsumed raw, reusing its exact `captured_at`,
  ordering the write against `SessionEnd` capture, and taking the project write lock
  (ADR-0023) — and **no current CLI or ingestion caller offers that operation.** That
  verb is the build-plan Backlog item this ADR unblocks. `authority` is what makes its
  output honest, not what makes it work.
- **An already-consumed session cannot be retrofitted.** `write_raw` raises
  `RawConsumedError` once the curator has flipped `consumed: true`, and the documented
  remedy is a fresh `captured_at` — which, by the point above, means a *new* raw, not a
  replacement. Wrap-time authoring is therefore only available to sessions whose raw is
  still unconsumed, which is the common case but not a guarantee.
- **Codex sessions are unaffected and stay skims.** `render_transcript` returns `None`
  for any agent but `claude` ([`curator/distill.py:200-205`](../../src/neurobase/curator/distill.py)),
  so Codex cannot distill today and will not author either unless Codex's own wrap
  performs the same write. On this machine that is roughly four sessions per project per
  day left at skim fidelity.
- **`doctor` gains something worth reporting.** A project whose recent raws are
  predominantly `agent-authored` is a project whose curated facts are largely a model's
  account of itself. That is a legitimate state to be in, and D47 keeps it from changing
  any weighting — but it should be *visible* rather than inferred, on the same reasoning
  ADR-0026 used for the denylist warning: `doctor` is already the read-only reporting
  path, and a hook cannot warn because its stdout is protocol output.
- **Deliberately not decided here.** (a) Whether authored digests are *better* — §10.3,
  still open, and D47 is written so that answering it later stays a separate decision.
  (b) Whether `/wrap` → vault and `neurobase wrap` → store derive from one act of
  authorship or duplicate it — the note's decision 8 recommends "derive, don't
  duplicate" (`neurobase wrap --summary -` fed by the summary `/wrap` already wrote),
  unratified. (c) Where authored files live durably — the vault's
  `projects/*/sessions/` is gitignored, so the five that exist today are single-copy.
  (d) The continuous-curation knobs (`stale_hours`, `auto_max_raws`), which the source
  note declines to specify on purpose after two drafts each promised a guarantee their
  mechanism could not deliver.
- **Process note.** This ADR is `Proposed`, not `Accepted`, and is not the routing of
  §11 in full — only the one slice that is decidable on evidence already in hand. The
  rest of §11 still needs walking piece by piece, which is the standing next action.
