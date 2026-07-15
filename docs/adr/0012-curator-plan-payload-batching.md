# ADR-0012: Curator plan payloads use byte-budgeted sequential batches

- **Status:** Accepted
- **Date:** 2026-07-14
- **Resolves:** S-cf4, D22 (capture-fidelity foundation)
- **Supersedes:** none

## Context

The v0.1 curator passed every unconsumed raw capture and every active fact to one
`brain.plan_json` call. CLI brains combine the system and user prompts into one
argv entry, so accumulated raws—or the proposed richer deterministic skim—could
eventually fail before the brain starts.

S-cf4 measured the local macOS boundary without invoking an LLM: `ARG_MAX` is
1,048,576 bytes; the current environment is 2,485 bytes; and a harmless
`/usr/bin/true` subprocess accepted a 1,045,268-byte argument but failed at
1,045,269. The boundary is measured in bytes, not characters, and varies with
the environment. Unicode and JSON escaping make a character limit unsafe.

## Decision

**D22:** Curator plan requests are capped by the UTF-8 byte length of the exact
combined CLI prompt (system prompt, separator, and serialized user payload).
The conservative default is 262,144 bytes, configurable as
`[curate].plan_payload_max_bytes`.

Raws are planned oldest-first in sequential batches. Each successful batch is
applied and consumed before the next; active facts are then reloaded so later
batches see all earlier upserts, supersession, and tombstones. A failed batch
and every later batch remain unconsumed, while earlier committed batches stand.
Node synthesis, pruning, indexing, and linkification run once after all batches.

**Derived state never lags committed facts.** Those once-per-pass steps run
whenever at least one batch committed — *including when a later batch failed and
the pass returns an error*. Skipping them on the error path was the first
version of this change and it was wrong: the node is what recall injects, and a
raw that fails the plan step *permanently* is re-planned by every retry, so the
committed facts would never reach recall at all. The pass still reports `error`;
the store is simply left self-consistent.
A single raw larger than the budget is deterministically truncated and marked;
it is never skipped silently. If the fixed prompt, current facts, and a marked
raw envelope cannot fit, curation returns an error without consuming that raw.

## Consequences

Typical passes remain one call with byte-identical v0.1 user payloads. Large
passes cost more plan calls and are no longer all-or-nothing across the entire
backlog, but each committed unit has a valid plan and durable state. Summaries
now include `batches`. Dry-run previews multiple independent batch plans without
pretending to simulate model-authored mutations in memory.

This safety foundation lands before richer capture or transcript distillation.
Those later changes can increase raw signal without reintroducing argv failure.

## Alternatives considered

- **Character-count cap** — rejected because UTF-8 and JSON serialization make
  it unrelated to the kernel's byte boundary.
- **Count-based batches** — rejected because raw sizes vary widely.
- **Send prompts over stdin** — not uniformly supported by the current CLI
  backend contracts and would not bound model context size.
- **Truncate the entire backlog into one request** — rejected because it
  silently drops complete sessions and prevents per-batch retry/consumption.
