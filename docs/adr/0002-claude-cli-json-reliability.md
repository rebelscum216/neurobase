# ADR-0002: `claude -p` JSON contract reliability for the curator

- **Status:** Accepted
- **Date:** 2026-07-07
- **Resolves:** S5
- **Supersedes:** none

## Context

The curator's `plan_json` brain call (spec §2, §2.1) needs `claude -p
--output-format json` to reliably return a parseable plan object (the
`{"upserts":[...], "tombstones":[...]}` shape). §11.3 had the envelope
structure live-verified from a single call (answer lives in the `.result`
string; `--max-turns 1` works) but the spike's remaining scope was explicitly
the 10-run reliability check, exit criterion **≥9/10 parse success** with the
lenient (fence-tolerant) parser from spec §2 step 3.

## Decision

Built a 10-run harness: a system prompt matching every requirement in spec
§2.1 (curator mandate, dedup/supersede/tombstone rules, exact JSON shape) plus
a realistic user payload (2 curated facts, 2 raw captures shaped like real
scribe output), invoked as:

```
claude -p "<system>\n\n---\n\nINPUT:\n<user payload>" --output-format json --max-turns 1
```

Each run's `.result` string was parsed with a fence-stripping (```json ... ```)
JSON parser and validated against the upserts/tombstones shape.

**Result: 10/10 runs produced valid, schema-conforming JSON.** Latency ranged
~3.6s–16.4s per call — comfortably inside the 120s brain-call timeout (spec
§8). No prompt or flag adjustment was needed to clear the bar; the design
already specified in §2.1/§11.3 works as-is.

## Consequences

- `brain/claude_cli.py`'s `plan_json` can be implemented directly from
  spec §2.1/§11.3: shell out to `claude -p <prompt> --output-format json
  --max-turns 1`, parse `.result` with the lenient parser, no retry-on-parse
  needed beyond the spec's existing "unparseable ⇒ abort pass, leave raw
  unconsumed" safety net (step 3).
- Fixture tests for the lenient parser can use this ADR's harness output as
  additional real-shaped samples alongside §11.3.
- Because sample size is 10 (not, say, 100), treat "parse failures are rare"
  as a working assumption backed by this spike, not a guarantee — the abort
  safety net exists precisely because a parse failure is expected to be
  possible, just uncommon.

## Alternatives considered

- **`--json-schema` flag** (raised as an open question in the original spike
  method) — not needed; a plain JSON-only instruction plus lenient parsing
  already cleared the reliability bar. Worth revisiting only if reliability
  degrades on more complex real-world payloads than this harness's fixture.
