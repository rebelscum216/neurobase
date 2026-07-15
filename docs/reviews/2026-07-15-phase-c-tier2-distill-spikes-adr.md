---
slug: phase-c-tier2-distill-spikes-adr
status: awaiting-review
author: claude
reviewer: codex
branch: phase-c-tier2-distill
diff: git diff main...HEAD
created: 2026-07-15
---

# Review: Phase C Tier-2 distill — S-cf4/S-cf5 spikes + ADR-0014

## Brief  _(Author — Claude)_

**Intent.** Close the two spikes that gate the Phase C (Tier-2 transcript
distill) contract, and draft the ADR that records the resulting decisions. This
is a **docs-only, design-stage** change — no source, no tests. It sets the
contract the A2/A3 implementation will be built and tested against. Companion
doc: `~/Downloads/neurobase-capture-fidelity-plan.md` (Part II §A2/§A3/§A5/§C).

**Scope.** Branch `phase-c-tier2-distill`, `git diff main...HEAD`. Key files:
- `docs/notes/spikes/S-cf4-argv-ceiling.md` — measured the real macOS `execve`
  argv ceiling (~1.02–1.05 MB) and confirms the shipped
  `plan_payload_max_bytes = 262_144` (ADR-0012) has ~3.9× headroom and is
  byte-budgeted. Probe: `scratchpad/scf4_argv_probe.py` (not committed).
- `docs/notes/spikes/S-cf5-distill-quality.md` — distilled two real transcripts
  through a first-draft `DISTILL_SYSTEM`; digest-vs-skim eyeball. Records three
  findings (F1 unenforced digest cap; F2 transcript-borne role hijacking; F3
  degrade-to-skim on failed validation, not only call errors).
- `docs/adr/0014-transcript-distill-curation.md` — **Proposed** ADR. D15
  (transcript_path/capture_version pointer, additive/optional), the curate-time
  distill step above ADR-0012 batching, `raw/.digests/` cache, D16
  degrade-never-abort, and the F1–F3 hardening baked into `DISTILL_SYSTEM`.
- `docs/adr/README.md`, `docs/notes/spikes/README.md` — index/registration.

**Focus areas.**
1. **Contract soundness of D15.** Are `transcript_path` + `capture_version`
   truly additive/backward-compatible? I claim they ride the `consumed: true`
   flip because `mark_consumed` (`src/neurobase/core/store.py:238`) copies all
   frontmatter and only flips the flag, and `write_doc` round-trips YAML with
   `sort_keys=False`. Verify that, and that no `STORE_SCHEMA_VERSION` bump is
   warranted (D11 guard interaction).
2. **`raw/.digests/` cache invisibility.** I claim `list_raw`
   (`store.py:220`) globs `raw/*.md` non-recursively, so the sidecar dir is
   invisible to the store contract. Confirm the glob doesn't recurse and nothing
   else enumerates `raw/` in a way the sidecar would pollute.
3. **Do F1–F3 fully close the S-cf5 risks**, or is there a distill failure mode
   the ADR still leaves able to reach the store / abort a pass? Especially:
   does "degrade to skim on failed validation" plus "only plan-parse aborts"
   preserve ADR-0012's D9 semantics exactly?
4. **N+2 cost framing.** I reframed it as N = chunks (not sessions) distilled
   per pass. Is that the right accounting, and are the stated mitigations
   (cache, staleness, `distill = "off"`) sufficient given the argv/rate limits?

**Known risks / tradeoffs.**
- The ADR is a **proposal**, deliberately not Accepted — it names the
  spec-appendix (§1/§2/§8/§10) as the law it must be folded into, and defers the
  planted-secret redaction guarantee to a Phase C "Done-when" test rather than
  proving it here.
- S-cf5 ran on my (the author's) live `claude` backend; the digest-quality claim
  is a single substantive-session eyeball plus one degenerate case, not a
  corpus. Treat "digest >> skim" as directional evidence, not a benchmark.
- The plan draft said 300 000 chars / 6 000-char digest cap; I reconciled to the
  shipped 262 144 **bytes** and flagged the 6 000 cap as unenforced (F1).

**How to verify.**
```
git diff main...HEAD
# spot-check the code claims the ADR/baton rest on:
sed -n '238,244p' src/neurobase/core/store.py     # mark_consumed preserves frontmatter
sed -n '220,235p' src/neurobase/core/store.py     # list_raw globs raw/*.md (non-recursive)
grep -n plan_payload_max_bytes src/neurobase/core/config.py   # shipped 262144
# reproduce the spikes if desired (S-cf5 makes real `claude -p` calls):
python3 scratchpad/scf4_argv_probe.py
```

**Out of scope.**
- No implementation exists yet (A2/A3 are the next branches) — don't flag
  "missing code/tests"; this review is the contract, not the build.
- Prose/style of the spike narratives. Flag contract or correctness problems,
  not wording.
- Re-litigating ADR-0012's batching decision (already Accepted) — only its
  interaction with the new distill step is in scope.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** _pending._
