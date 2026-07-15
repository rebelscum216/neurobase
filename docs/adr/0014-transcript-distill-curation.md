# ADR-0014: Transcript-distill curation (Tier-2 capture fidelity)

- **Status:** Proposed
- **Date:** 2026-07-15
- **Resolves:** S-cf5; D15, D16, D17 (capture-fidelity Part II, plan §A2/§A3/§A5)
- **Supersedes:** none

## Context

Capture is a deterministic, no-LLM **skim + pointer** (ADR-0003 latency budget,
ADR-0013 event shapes). Even with the Tier-1 richer skim, the store keeps a
bounded projection of each session: the real discovery stated two hours before
the final message, a subagent's full reasoning, the exact tool outcomes — all
still live only in the agent's on-disk session transcript, which the scribe
already reads at capture time.

The capture-fidelity plan's answer is **not** to copy transcripts into the store
(they run multiple MB, dominated by tool results; that blows the curator's argv
budget, multiplies the redaction leak surface, and duplicates substrate that
already exists on disk). Instead: give the **curator** access to the transcript
*while it still exists*, distill it to a compact digest with the LLM that is
already in the loop, and fold digests — not skims — into the plan step. Capture
stays a fast pointer; extraction moves to the layer that has an LLM, no latency
budget, and chunking freedom.

Two spikes gated this contract:

- **S-cf4** (see the [spike note](../notes/spikes/S-cf4-argv-ceiling.md) and
  ADR-0012) confirmed the shipped `plan_payload_max_bytes = 262_144` sits ~3.9×
  under the real macOS `execve` ceiling and is correctly byte-budgeted. The
  distill step layers *above* that batching; it does not change it.
- **S-cf5** (the [distill-quality probe](../notes/spikes/S-cf5-distill-quality.md))
  distilled two real transcripts through a first-draft `DISTILL_SYSTEM`. On a
  substantive session the digest was decisively richer than the skim — decisions
  *with their why*, non-obvious gotchas, state changes with SHAs — with no
  invention. It also surfaced three failures that shape this contract:
  - **F1** — the 6 000-char digest cap was unenforced; the merge path returned
    7 886 chars.
  - **F2** — transcript content **hijacked the distiller's role**: on
    neurobase's own curator session (whose text embeds the curator/distill
    prompts verbatim), the model answered conversationally instead of emitting a
    digest. Transcript-borne text that looks like instructions is a live
    injection surface, and dogfooding amplifies it.
  - **F3** — a *successful* brain call can still return an unusable digest, so
    degrade-to-skim must trigger on failed validation, not only on call errors.

## Decision

Add a per-session **distill** step to `curate()`, between loading unconsumed
raws and building plan batches, plus the raw-frontmatter pointer it needs. The
transcript is never copied into the store.

**D15 — Transcript access by pointer.** Raw frontmatter gains two optional,
additive keys (absent ⇒ a v1 raw; every reader tolerates absence):

```yaml
transcript_path: /abs/path/to/session.jsonl   # Claude: hook transcript_path; Codex: rollout path
capture_version: 2
```

They ride through the `consumed: true` flip unchanged — `mark_consumed` copies
all frontmatter and only flips the one flag (`store.py:238`), and `write_doc`
round-trips YAML with `sort_keys=False`. No `STORE_SCHEMA_VERSION` bump: the
keys are additive and optional, so older binaries ignore them (spec §1). The
path is stored absolute; resolution is best-effort and a missing/unreadable/
moved path silently degrades to the skim — a wrong path is never an error.

**Distill step (per unconsumed raw with a resolvable `transcript_path`, when
`[curate].distill != "off"`):** render the transcript to compact text (prompts,
all assistant texts, `tool_use` one-liners, `tool_result` bodies truncated to
2 000 chars each; sidechains **included** — subagent context is cheap here),
**redacting each extracted value *before* it is labelled/truncated into the
render, and re-redacting the whole render as defense in depth (D17, below)**,
chunk at `distill_chunk_chars` (200 000), and call `brain.text(DISTILL_SYSTEM, chunk)`
per chunk, capped at `MAX_DISTILL_CHUNKS = 5` (drop middle chunks first, noting
the drop in the digest header); `> 1` chunk ⇒ a final merge call. The digest
**replaces the raw's body** in that batch's `raw_captures` entry — same `raw`
filename key, so `from_raw` provenance is unchanged — and passes through
`redact()` again (defense in depth) before it is cached or enters any payload.
Batching, byte budget, and D9 per-batch abort semantics are exactly ADR-0012's;
distill only changes what sits in each entry's body.

**D17 — Redact the transcript before it leaves for the brain, not just the
digest** _(added in review — closes a real gap the plan's §6 trust note missed)._
Raw skim bodies are already `redact()`-ed at capture, so today's plan call only
ever sends redacted text to the brain. Rendering a transcript straight into
`brain.text()` would send **unredacted** `tool_result` bodies — exactly where
tokens, env vars, and connection strings live — to the configured brain. The
plan justified this as "the same logged-in CLI that produced the session," but
that assumption does not hold: neurobase is cross-agent, so a **Codex** session's
transcript can be distilled by a `claude`/`anthropic-api` brain (or vice-versa),
and the API backend is a different credential/endpoint than the CLI that made the
session. So the transcript render is redacted before every distill/merge call,
holding the store's existing "only redacted text leaves" guarantee.

**Redact per value, before rendering — not the finished render.** This follows
the scribe's own rule (spec §10, ADR-0013 finding 2): D13's env-assignment rule
is **line-anchored**, so prepending a structural label/prefix (`ASSISTANT:`,
`[tool_result] `, a bullet, a fence, the untrusted-data delimiter) *before*
redacting shifts an `API_TOKEN=…` / `PASSWORD=…` off column 0 and shields it from
the line-anchored pattern — exactly the shape that leaked in the scribe until it
was fixed. `tool_result` bodies are the transcript's likeliest carrier of `.env`
dumps and shell output, so this is where it bites. Therefore each extracted value
is redacted **before** it is labelled or truncated into the compact render:
prompts / assistant text / `tool_result` bodies via `redact()`, and command-shaped
values (the Bash `tool_use` one-liners) via `redact_command()`, mirroring the
scribe's `scrub` / `scrub_command` split. The whole-render `redact()` and the
second pass over the digest remain as defense in depth, not the primary line.
Redaction is best-effort (regex table, D13) — the same level the store already
ships. Sending unredacted transcripts is **not** offered even as an opt-in here;
if a future need arises it gets its own ADR + SECURITY treatment.

**Digest cache (content-addressed).** Digests are written to
`raw/.digests/<raw-filename>` so a failed or aborted pass never re-distills. But
the filename alone is **not** a safe key: a raw is rewritable by its owning
scribe until `consumed: true`, and the Codex scribe deliberately *overwrites one
raw per session in place* every turn (last-turn-wins,
`codex/scribe.py`, `captured_at = session start`). So a distill that succeeds and
caches, in a pass that then aborts before consuming that raw, would leave a stale
digest under a filename a later, longer turn reuses — and the next pass would
fold the old digest and silently drop the newer session
_(finding — closes a real stale-cache hole)._

The cache entry therefore carries a **`source_fingerprint`** of the exact distill
input — the raw body's content hash **and** the transcript's fingerprint (path +
size + mtime, or a content hash). On read, the fingerprint is recomputed and must
match; a mismatch or a missing fingerprint is a cache **miss** ⇒ re-distill. This
invalidates on both a rewritten raw body and a grown/replaced transcript.
`list_raw` globs `raw/*.md` non-recursively, so the sidecar directory stays
invisible to it and to the store contract; the cache is derived state, safe to
delete (a purge just costs one re-distill). It is a sidecar, not a
raw-frontmatter edit, so the owning-scribe mutability rule stays clean.
**Dry-run never writes the cache** (it applies nothing and must not persist
derived state); a dry run may read a valid cached digest but a miss simply
re-distills for the preview.

**D16 — Distill failure degrades, never aborts.** Any distill failure — missing
or unreadable transcript, brain error, timeout, **or an output that fails
validation (F2/F3)** — falls back to that raw's skim body and the pass continues.
Only plan-parse failures abort a batch (D9 / ADR-0012, unchanged). Per-raw
outcomes are logged in the pass summary (`distilled: n, fallback: m`).

`DISTILL_SYSTEM` hardening, forced by S-cf5 (write the prompt text to meet
these):

1. **Bounded output, deterministically enforced (F1).** The prompt requests
   ≤ `DIGEST_MAX_CHARS` (6 000), *and* the pipeline hard-truncates any longer
   digest with a `[digest truncated]` marker after the call. The model's
   adherence is not trusted; the digest replaces the raw body in a byte-budgeted
   payload, so its size must be bounded in code.
2. **Untrusted-data fence (F2).** The rendered transcript is wrapped in an
   explicit delimiter and the system text states that everything inside is a
   transcript to summarize — never instructions to follow — including any text
   that reads as a system prompt, a role assignment, or a request. The fence is
   load-bearing when neurobase distills its own sessions (whose text embeds the
   curator/distill prompts verbatim). Note the brain that reads the transcript is
   still whatever backend the user configured — per D17 the render is redacted
   first, so what crosses is redacted transcript text, not raw tool output.
3. **Output-shape validation (F3).** A digest that lacks the expected structure
   (the required headings) or that reads as a refusal/question is treated as a
   distill failure and triggers the D16 skim fallback. Validation is a shape
   check, not a quality judgment.

**Config (plan §A5):**

```toml
[curate]
distill = "auto"            # auto | off  (auto = distill when transcript_path resolves)
distill_chunk_chars = 200000
# plan_payload_max_bytes stays 262144 (ADR-0012)
```

Tier-3 substrate copy (`[capture] keep_transcript`) is **not** specced here —
parked pending its own ADR (plan Phase D).

## Consequences

- Curate cost goes from 2 brain calls/pass (plan + node) to **N + 2**, where N
  counts *chunks distilled this pass*, not sessions — S-cf5 showed a single
  0.62 MB session is already 2 distill + 1 merge = 3 calls. The digest cache
  (never distill twice), the 12 h staleness cadence, and `distill = "off"`
  bound the rate-limit exposure (plan §F).
- The store stays small and redacted: only bounded, redacted digests can reach
  the plan payload or the cache; the transcript itself never lands anywhere in
  the store, and per D17 only redacted transcript text is ever sent to the brain.
  The planted-secret guarantee now has two Phase C **Done-when** tests: a fake
  secret in a tool result never appears in `raw/.digests/` or `curated/`, **and**
  never appears in the text handed to the (fake) brain's distill/merge calls.
- v1 raws (no new keys) and `distill = "off"` both behave exactly as today —
  the skim body is the payload. Tier-2 is purely additive over ADR-0012.
- **Spec appendix** must gain: §1 the two optional raw keys + the `.digests/`
  sidecar exclusion + the content-addressed cache fingerprint; §2 the distill
  step in the curate sequence, the D17 per-value redact-before-render rule
  (`redact`/`redact_command`, mirroring the scribe), and the D16 failure policy; §8 the new defaults; §10 the `[curate]` config keys. **This
  ADR is the proposal; that appendix is the law** — fold these in when
  implementing. SECURITY.md gains the note that **only redacted** transcript text
  is sent to the user's configured brain (D17), the transcript itself never lands
  in the store, and digests are redacted.

## Alternatives considered

- **Copy the full transcript into the store (Tier 3 now)** — rejected here:
  multi-MB bodies blow the argv budget, multiply the redaction leak surface, and
  duplicate on-disk substrate for durability we don't yet need. Parked as
  Phase D with its own ADR + SECURITY treatment.
- **Distill at capture time** — rejected: reintroduces an LLM into the hook,
  breaking the ADR-0003 latency budget and exit-0 fail-safety. Extraction
  belongs where there is already an LLM and no latency budget.
- **Trust the model to honor the 6 000-char cap and its role** — rejected by
  S-cf5 F1/F2 directly: the merge overran the cap and transcript text hijacked
  the role. Both are enforced in code, not just requested in the prompt.
- **Bump `STORE_SCHEMA_VERSION` for the new keys** — unnecessary: additive
  optional frontmatter is forward/backward compatible; a bump would trip the
  D11 guard for no gain.
- **Send the transcript to the brain unredacted** (as the plan's §6 note
  implied, or as an opt-in) — rejected (D17): raw skim bodies are already
  redacted before they reach the brain today, and neurobase is cross-agent, so a
  Codex session's transcript could be sent to a `claude`/API brain — a different
  credential and endpoint than the CLI that produced it. Unredacted sending would
  regress the store's "only redacted text leaves" guarantee for no functional
  gain (a digest should never need secrets). Not even offered as opt-in here.
- **Key the digest cache by raw filename only** (first draft) — rejected: the
  Codex scribe overwrites a raw in place each turn, so a filename can map to
  different bodies over time; a fingerprinted, content-addressed cache is the fix.
