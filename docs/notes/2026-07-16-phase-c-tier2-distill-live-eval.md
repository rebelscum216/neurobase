# 2026-07-16 — Phase C Tier-2 distill live eval

What this is: closeout evidence for the Phase C Tier-2 distill implementation
after the review relay approved `phase-c-tier2-distill`.

## Goal

Drive the **real implemented path**, not the older S-cf5 probe:

1. Claude scribe writes a v2 raw into a temporary store, including
   `transcript_path` and `capture_version: 2`.
2. `curator.distill.distill_docs(...)` reads that raw, renders the referenced
   Claude transcript, calls a real CLI brain, and substitutes the digest body.
3. A second `distill_docs(...)` pass hits the digest cache sidecar rather than
   calling the brain again.

The eval used a temporary Neurobase store only. It did not mutate the dogfood
store under `~/neurobase`.

## Claude backend attempt

Claude started the same eval with `ClaudeCLIBrain`, but the live CLI backend was
temporarily unavailable:

```text
api_error_status: 429
result: You've hit your session limit · resets 12:30pm (America/New_York)
```

That explains the initial `{'distilled': 0, 'fallback': 1}` result: D16 worked
as designed, degrading to the skim when the configured brain failed. The
Claude-specific live eval should be retried after the reset if we want evidence
for that exact backend.

## Completed eval with CodexCLIBrain

Codex CLI was available and was exercised through the same `Brain` interface the
curator uses in production. The transcript was a real Claude Code JSONL session
from the neurobase project, basename
`0244675a-9a89-471d-8354-e5524497ed97.jsonl`.

Observed output:

```text
transcript bytes: 341614
rendered chars: 39893
transcript_path_recorded: True
capture_version: 2
pass1_counts: {'distilled': 1, 'fallback': 0}
brain_calls_after_pass1: 1
pass2_counts: {'distilled': 1, 'fallback': 0}
total_brain_calls: 1
cache_hit: True
cache_sidecar: True
skim_chars: 7696
digest_chars: 3786
digest_has_expected_heading: True
```

The digest had the expected ADR-0014 headings, starting with `## Decisions` and
`## Discoveries & gotchas`, and was shorter than both the rendered transcript
and the stored skim while preserving higher-level decisions and gotchas from the
session.

## Result

The implemented A2/A3 path is live-verified with a real CLI brain and a real
Claude transcript in a temp store:

- v2 raw pointer capture worked.
- Transcript rendering worked.
- `distill_docs(...)` substituted a validated digest.
- The digest sidecar cache was written and reused on the second pass.
- The dogfood store was not touched.

## ClaudeCLIBrain rerun

The backend-specific rerun with `ClaudeCLIBrain` succeeded later on
2026-07-16, using the same real Claude transcript and a temporary Neurobase
store only. The dogfood store was not touched.

Observed output:

```text
transcript bytes: 341614
rendered chars: 39893
transcript_path_recorded: True
capture_version: 2
pass1_counts: {'distilled': 1, 'fallback': 0}
brain_calls_after_pass1: 1
pass2_counts: {'distilled': 1, 'fallback': 0}
total_brain_calls: 1
cache_hit: True
cache_sidecar: True
skim_chars: 7715
digest_chars: 3297
digest_has_expected_heading: True
```

The digest started with `## Decisions`, so it passed ADR-0014 shape validation.
This closes the earlier optional Claude-backend follow-up.
