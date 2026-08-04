# ADR-0027: `authority` on a raw — an agent-authored digest is a distinct capture class, on schema 1

- **Status:** Proposed
- **Date:** 2026-08-04
- **Resolves:** decision 6 ("ship the session slice schema-1-only") of the
  memory-layering working note, 2026-08-01, §9.4/§10.2/§11.4. ⚠️ **That note is
  unpublished** — it is a private lab notebook, not committed to this repo, so the
  section references in this ADR are provenance for the author and are **not
  inspectable by a reader of the public repo**. The committed, inspectable evidence for
  the claims below is the code cited inline plus
  [`docs/notes/spikes/2026-08-04-stance-test.py`](../notes/spikes/2026-08-04-stance-test.py)
  and its write-up. Nothing in this ADR's Decision section depends on the note.
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
be improved: distill chunks a rendered transcript that later passes do not re-read — the
digest cache, not the prompt cache, is what prevents re-distilling — so across clean
session-distill calls the chunks are normally unique and the prompt cache is written
without being read back. (Stated as a strong tendency rather than a guarantee: a retry
re-reads its own prompt, which is exactly what produced the 9.34% artifact above, and
identical content across sessions is not mechanically impossible.) Per-capture distill
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
is not exposed to **`G10`** at all. One bound survives: a raw whose body alone exceeds
`plan_payload_max_bytes` is body-truncated (with `OVERSIZE_RAW_MARKER`) by
`_truncate_raw_to_fit`, selected from `_next_plan_batch` when a single raw cannot fit a
batch ([`curator/engine.py:145-200`](../../src/neurobase/curator/engine.py)). So "the
fold sees the body" is true up to that ceiling.

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
does not wait for schema 2.** The reason is that **D11 governs the store's identity, not
its documents' keys**: the schema comparison lives in `open_store` and only there
([`core/store_handle.py:366-425`](../../src/neurobase/core/store_handle.py)), and what
it compares is the integer in `store.toml`. An additive optional key on a *raw* is
outside that comparison entirely, and current readers already ignore unknown raw
frontmatter. The precedent is ADR-0014, which introduced `capture_version: 2` as a
**per-document** marker while `STORE_SCHEMA_VERSION` stayed `1`
([`core/store.py:29`](../../src/neurobase/core/store.py)); `write_raw`'s contract
already requires readers to tolerate its absence.

  For accuracy about the guard itself, since an earlier draft of this ADR overstated it
  (review finding `P2-DOCS-PLAN-ACCURACY-003`): `open_store` does **not** merely refuse
  a newer schema. `_parse_schema` fails **closed** on unreadable or non-integer
  metadata for every mode except `PURGE`, which opens regardless so a corrupt or newer
  store stays deletable (D25); and a **`DOCTOR`** handle carries a newer integer instead
  of raising, so the condition can report it. None of those paths are reached by adding
  a document key — which is the point — but "refuses only a newer schema" was wrong as
  written.

  **On not waiting for ADR-0016.** ADR-0016 is accepted-but-unimplemented, and its
  rationale is **independent of this one**: it exists for the hardening work's
  trust boundary — per-project policy, a partition primitive, profiles, stable event
  IDs, content hashes
  ([`0016-store-schema-2-project-records-profiles.md:10-36`](0016-store-schema-2-project-records-profiles.md)).
  Nothing in D45–D48 contradicts it or is blocked by it. What this ADR declines is
  *routing `authority` through schema 2's record/source identity model*, which would
  make that model's shape partly answerable to a premise — that authored digests are
  worth having — which nothing has yet tested. **Migration path:** `authority` is an
  optional raw-frontmatter key, so a schema-1→2 migration carries it forward unchanged
  or maps it onto whatever source-identity field schema 2 defines; either way it is one
  optional key, and no migration step depends on its value. If schema 2 later supersedes
  it, that supersession is a one-line ADR annotation, not a data migration.

**D47 — `authority` is a claim about provenance, not a grant of trust.** It MUST NOT
raise (or lower) a document's weight in planning, ranking, recall, or proposal mining.
Its permitted consumers are only the surfaces that would otherwise *misdescribe* what
they hold: the fold journal, `doctor`, and any UI that names the rung it is showing. If
some later change wants authored bodies weighted differently, that is its own ADR with
its own evidence.

  **This is enforced structurally, not by assertion** (review finding
  `P2-TEST-GAP-004`; an earlier draft claimed adding the field "cannot quietly become a
  policy change" while requiring no mechanism, which proves nothing once the journal,
  `doctor` and UI consumers exist). Accepting D45 therefore carries these implementation
  requirements, and they are acceptance criteria for the Backlog verb — not advice:

  1. **Update the spec appendix's raw-schema section** with the optional key, its
     default-when-absent, and this constraint, so the boundary is contract rather than
     ADR prose (`AGENTS.md` treats appendix MUSTs as law).
  2. **Preserve the existing planner seam.** `_raw_payload`
     ([`curator/engine.py:123-124`](../../src/neurobase/curator/engine.py)) already
     projects a raw to `{"raw": <filename>, "body": <body>}` — it passes **no**
     frontmatter into the plan payload. `authority` MUST stay outside it. That seam is
     the mechanism; naming it here makes removing it a visible change.
  3. **Pin it with paired fixtures.** Two raws identical but for `authority` MUST
     produce byte-identical plan payloads, identical selection, and identical
     rank/recall/proposal-scoring inputs; the only permitted difference is
     journal/`doctor`/UI reporting metadata. A test that fails when toggling only
     `authority` changes any planning-side input is what makes D47 real.

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
  raw is addressed by the **whole** `(captured_at, agent, sid8(session_id))` tuple at
  microsecond precision, not by `session_id` alone and not by `captured_at` alone
  ([`raw_filename`, `core/store.py:200-211`](../../src/neurobase/core/store.py)) —
  an earlier draft of this ADR wrote "keyed by `captured_at`, not `session_id`", which
  is loose in a way that matters to an implementer (review finding
  `P2-DOCS-PLAN-ACCURACY-002`). The accurate statement is narrower: **`session_id` alone
  cannot address an existing raw**, because the timestamp component varies per capture.
  `raw_filename`'s own comment scopes the session-keyed overwrite trick to *"a scribe
  reusing a **stable** `captured_at`"*, and the Claude scribe reuses nothing — it passes
  a fresh `datetime.now(UTC)` on every capture
  ([`adapters/claude/scribe.py:347`](../../src/neurobase/adapters/claude/scribe.py)).
  So a naive wrap-time write **adds** a second raw for the session rather than replacing
  its skim, and the fold would see the session twice. Live evidence, previously misread
  as harmless redundancy: the `transactiontracker` project holds **four separate raws
  for one session** (`499ca1a4`) at four timestamps. Doing this correctly requires a
  **lookup contract this ADR does not define**, and which the Backlog verb must specify
  before implementation:
  - how the verb obtains and proves the **full** `session_id` *and* `agent` — the
    filename carries only `sid8`, so the tuple cannot be reconstructed from a raw's name
    alone;
  - which candidate it replaces when the session has **zero, one, or several**
    unconsumed raws (the four-raw case above is real, not hypothetical);
  - how the later `SessionEnd` capture is prevented from reintroducing a duplicate after
    the wrap-time write;
  - ordering and mutual exclusion via the project write lock (ADR-0023);
  - what happens when the only match is already consumed (see the next bullet).

  **No current CLI or ingestion caller offers that operation.** That verb is the
  build-plan Backlog item this ADR unblocks; `authority` is what makes its output
  honest, not what makes it work. Acceptance tests should cover: same timestamp /
  different session, repeated captures under one full `session_id`, an already-consumed
  match, and the wrap-before/after-`SessionEnd` race.
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
- **Deliberately not decided here — and the Backlog entry says the same.** (a) Whether
  authored digests are *better* (the note's §10.3), still open; D47 is written so that
  answering it later stays a separate decision. (b) **The CLI interface and the
  `/wrap` relationship.** Whether `/wrap` → vault and `neurobase wrap` → store derive
  from one act of authorship or duplicate it is **unratified**; `neurobase wrap
  --summary -` fed by the summary `/wrap` already wrote is the note's decision-8
  *recommendation*, **not a settled contract**, and by `P2-DOCS-PLAN-ACCURACY-002` it is
  in any case insufficient on its own — it carries no session or agent identity. The
  build-plan Backlog entry states the same status; if the two ever disagree, **this ADR
  is not the authority** — the interface needs its own ratified decision before
  implementation. (c) Where authored files live durably — the vault's
  `projects/*/sessions/` is gitignored, so the five that exist today are single-copy.
  (d) The continuous-curation knobs (`stale_hours`, `auto_max_raws`) — see
  [`G12`](../known-gaps.md), which is deliberately fix-neutral.
- **Process note.** This ADR is `Proposed`, not `Accepted`, and is not the routing of
  §11 in full — only the one slice that is decidable on evidence already in hand. The
  rest of §11 still needs walking piece by piece, which is the standing next action.
