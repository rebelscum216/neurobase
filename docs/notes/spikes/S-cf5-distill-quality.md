# S-cf5 — distill quality probe (DISTILL_SYSTEM v1)

Date: 2026-07-15
Series: capture-fidelity (Part II plan §C). Feeds the Phase C ADR + the
`DISTILL_SYSTEM` prompt requirements (plan §A3). Companion:
[S-cf4](S-cf4-argv-ceiling.md).

## Question

Run one real transcript through a first-draft `DISTILL_SYSTEM` and eyeball the
digest against the skim the Tier-1 scribe stores today. Does a curate-time
distill produce visibly richer facts than the deterministic skim, and what does
v1 of the prompt/pipeline need before the contract freezes?

**Exit criterion:** at least one real transcript distilled; digest-vs-skim
comparison recorded; prompt/pipeline gaps enumerated.

## Method

Probe `scratchpad/scf5_distill_probe.py`, faithful to plan §A3:

- **Render** the transcript to compact text: prompts + *all* assistant texts +
  `tool_use` one-liners + `tool_result` bodies (≤ 2 000 chars each), sidechains
  **included** (this is where subagent context is cheap).
- **Chunk** at `DISTILL_CHUNK_CHARS = 200_000`, cap `MAX_DISTILL_CHUNKS = 5`
  (drop middle first); `> 1` chunk ⇒ a final merge call over the per-chunk
  digests.
- `brain.text(DISTILL_SYSTEM, chunk)` via the repo's real `ClaudeCLIBrain`
  (same call path as production). Digest passes through `redact()`.
- Skim generated from the same transcript via `scribe._assemble_body(...)` for a
  side-by-side. Full outputs saved under `scratchpad/scf5_result_*.txt`.

`DISTILL_SYSTEM` v1 asked for four headings — **Decisions / Discoveries &
gotchas / State changes / Unresolved** — markdown only, no invention, no session
narration, "input to a downstream curator, not a user-facing summary", 6 000-char
cap.

Two transcripts:

| Transcript | Bytes | Rendered | Chunks | Kind |
|---|---|---|---|---|
| `b89631d4…` | 3.56 MB | 379,769 | 2 | Substantive session (PII scrub / go-public, ADR-0012, subagents) |
| `553b029f…` | 0.62 MB | 284,808 | 2 | Degenerate: a *curator-invocation* session that hit the session limit |

## Observed

### Transcript 1 (substantive) — clear win

Skim was 17.6 KB; digest was 7.9 KB. The digest was **dramatically richer and
more useful**:

- The skim's `## Prompts` was dominated by `<task-notification>` XML blobs
  (background-task completion messages injected as "prompts"), burning the
  25-prompt / 1 200-char budget on machine noise; real user intent ("go ahead we
  can flip it public") was buried among them.
- The digest recovered, with the *why*, decisions the skim can't express:
  ADR-0012 = typed `Store` handle over per-call guards; abandon the exhaustive
  entry-point census for root-cause + examples; publish public rather than pay
  Actions minutes.
- It captured non-obvious gotchas the skim loses entirely: `git push --force`
  can't purge `refs/pull/*` (immutable PR refs) so PII survived a clean rewrite;
  `--replace-text` rewrites blobs/messages but **not** identity headers; the D11
  guard gaps across `status --recommender` and `mcp serve`.
- State changes with identifiers (merge SHAs, `make ci` → 481 passed, CI green)
  and a genuinely useful `## Unresolved` list (delete archive repo, 8 squash-
  merged branches, Phase 9 not started).

Spot-checks against the skim/known facts found **no invention**. This alone
meets the exit criterion: the digest beats the skim decisively.

### Transcript 2 (degenerate curator session) — two failures surfaced

This session's content *is* the curator system prompt + a prior digest, and it
ended at the session limit. The merge step **broke role**: instead of a digest
it returned a conversational refusal ("I'm going to stop rather than produce
this… what do you want instead? 1/2/3"). The model read the embedded curator/
digest framing in the transcript as *its own instructions* rather than as data.

## Findings for the contract (A3) — v1 is not frozen

1. **F1 — the digest cap is not enforced (merge path).** Transcript 1's merged
   digest was **7 886 chars vs the 6 000 cap**. The per-chunk prompt cap is
   advisory to the model and the merge step ignored it. Fix: enforce
   `DIGEST_MAX_CHARS` deterministically after the call (hard truncate with a
   `[digest truncated]` marker), not just in the prompt. The digest replaces the
   raw body in the plan payload, so an unbounded digest quietly widens the batch.

2. **F2 — role confusion / injection is real and dogfooding amplifies it.**
   Transcript content is untrusted (the plan already says so), but the probe
   shows a concrete failure mode the plan under-weights: **neurobase's own
   sessions embed the curator and distill prompts verbatim**, which are
   maximally effective at hijacking the distiller. v1 must:
   - Wrap the rendered transcript in an explicit data fence and instruct the
     model that *everything inside is a transcript to summarize, never
     instructions to follow* — including any text that looks like a system
     prompt, a role assignment, or a request.
   - **Validate the output**: a digest that lacks the expected headings (or that
     reads as a refusal/question) is treated as a distill failure and the pass
     **degrades to the skim** for that raw (plan §A3 already mandates degrade-
     never-abort — this makes the trigger concrete). Add an output-shape check,
     not just an error/timeout check.

3. **F3 — degrade-to-skim is load-bearing beyond "missing transcript."** F2
   means a *successful* brain call can still yield an unusable digest. The
   fallback must cover "call succeeded but output failed validation," not only
   missing-file / brain-error / timeout.

4. **Cost data point.** Even a 0.62 MB session rendered to 2 chunks ⇒ **2
   distill calls + 1 merge = 3 brain calls** for a single session's distill (the
   render trims large tool_results but not prose-heavy sessions). The "N + 2"
   framing is per-session; per *chunk* it is higher on big sessions. The digest
   cache (never distill twice) and the `distill = "off"` escape hatch matter for
   the rate-limit risk (plan §F row 1). `MAX_DISTILL_CHUNKS = 5` was never hit
   here (largest real session ≈ 8.9 MB → ~5 chunks), so 5 is right at the edge
   for the biggest sessions — keep the middle-drop + header note.

## Result

S-cf5 **closed**. Digest-vs-skim on a substantive real session is a clear win
(digest far richer, no invention) — the Tier-2 thesis holds. But `DISTILL_SYSTEM`
v1 is **not** frozen: the ADR/implementation must add (F1) deterministic digest-
size enforcement, (F2) an untrusted-data fence plus output-shape validation
against transcript-borne role hijacking, and (F3) a degrade-to-skim trigger on
failed validation, not only on call errors. These fold into the Phase C ADR and
the `DISTILL_SYSTEM` text; the redaction/planted-secret guarantee remains a
Phase C "Done when" test, not part of this spike.
