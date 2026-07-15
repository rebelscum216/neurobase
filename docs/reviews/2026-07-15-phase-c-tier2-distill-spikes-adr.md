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

- **major** — `docs/adr/0014-transcript-distill-curation.md:68` — The distill
  contract sends the rendered transcript chunks to `brain.text(...)` before any
  D13 redaction is specified, and only redacts the resulting digest before cache
  or plan-payload use. That means a planted secret in a tool result would still
  be sent to the configured brain under the default `distill = "auto"` path,
  even though today's curator only sends already-redacted raw bodies. The later
  "does not appear in raw/.digests/ or curated/" test would not catch that
  exposure. Suggested direction: either require `redact()` on the rendered
  transcript/chunks before every distill/merge brain call, or make the ADR and
  config default explicitly opt-in for sending unredacted transcripts to the
  user's brain.
  - **resolution:** _resolved (round 1)._ Confirmed real and worse than stated:
    neurobase is cross-agent, so a Codex session's transcript could be sent to a
    `claude`/`anthropic-api` brain — a different credential/endpoint than the CLI
    that produced it, so the plan's "same logged-in CLI" rationale doesn't hold.
    Added **D17**: the rendered transcript is `redact()`-ed before it is chunked
    or sent, on every distill/merge call (digest still redacted again, defense in
    depth). Unredacted sending is not offered even as opt-in (own ADR if ever).
    The planted-secret Done-when test now also asserts the secret never reaches
    the (fake) brain's distill/merge input. ADR §Decision (D17), §Consequences,
    §Alternatives updated.

- **major** — `docs/adr/0014-transcript-distill-curation.md:81` — The proposed
  cache key is only `raw/.digests/<raw-filename>`, but raw files are still
  rewritable by the owning scribe until `consumed: true` (the Codex
  last-turn-wins overwrite trick depends on that). If a distill succeeds and
  writes a cache, then the pass aborts before consuming the raw, a later turn can
  overwrite the same raw filename while the stale digest remains. The next
  curate pass would fold the old digest and silently lose the later session
  content. Suggested direction: make cache validity depend on the raw/transcript
  content being distilled (for example a digest metadata file with raw body and
  transcript fingerprint, or a hash-named cache entry), and specify dry-run/cache
  behavior accordingly.
  - **resolution:** _resolved (round 1)._ Confirmed against `codex/scribe.py`
    (`captured_at = session start` ⇒ stable filename, in-place per-turn
    overwrite). The cache is now **content-addressed**: each entry carries a
    `source_fingerprint` of the raw body content hash **and** the transcript
    fingerprint (path + size + mtime, or content hash); read recomputes and must
    match, else it is a miss ⇒ re-distill. This invalidates on both a rewritten
    raw body and a grown/replaced transcript. Dry-run never writes the cache (may
    read a valid one; a miss just re-distills for the preview). ADR §Decision
    (Digest cache) + §Alternatives updated.

## Round-1 resolution summary  _(Author — Claude)_

Both `major` findings confirmed real and **resolved** in a follow-up commit (no
amend/rebase of the reviewed commits). Changes are ADR-only (still docs, no
source): added D17 (redact-before-brain) and made the digest cache
content-addressed; updated Consequences, Alternatives, and the spec-appendix fold
list. Re-arming for round 2.

**Verdict:** changes-requested — _round 1; addressed, re-submitted._
