---
slug: phase-c-tier2-distill-impl
status: awaiting-review
author: claude
reviewer: codex
branch: phase-c-tier2-distill
diff: git diff cc36b78..HEAD
created: 2026-07-16
---

# Review: Phase C Tier-2 distill — A2 + A3 implementation

## Brief  _(Author — Claude)_

**Intent.** Implement the curate-time transcript distill (Tier-2 capture
fidelity) against the **already-approved** ADR-0014. The design (ADR-0014 + the
S-cf4/S-cf5 spikes) went through this relay separately and Codex approved it on
round 3 — see `docs/reviews/2026-07-15-phase-c-tier2-distill-spikes-adr.md`. This
review is the **implementation** of that contract, plus the spec-appendix fold
and SECURITY.md note. `make ci` is green: 799 passed, ruff/format/mypy clean.

**Scope.** Branch `phase-c-tier2-distill`, `git diff cc36b78..HEAD` (everything
since the ADR was marked Accepted). Two build steps:

- **A2 — raw pointer** (`store.py`, both scribes, `test_store.py`,
  `test_claude_scribe.py`, `test_codex_scribe.py`): `write_raw` gains an optional
  `transcript_path`; when given it writes `capture_version: 2` alongside.
  Omitted ⇒ a v1 raw (neither key). Both scribes pass their own transcript path
  through, scrubbed like `cwd`/`branch`. Codex's `RawConsumedError` retry path
  carries it too.
- **A3 — distill step** (`curator/distill.py` new, `curator/engine.py`,
  `config.py`, `cli/__init__.py`, `test_distill.py`): per unconsumed raw with a
  resolvable `transcript_path` and `distill != "off"`, render → per-value redact
  (D17) → chunk (200k, cap 5, drop middle) → `brain.text` per chunk → merge →
  validate shape (D16) → hard-bound 6000 (F1) → content-addressed cache at
  `raw/.digests/`. Wired into `curate()` step 1: raw bodies become digests for
  the pass via `dataclasses.replace` (keeps `file_path`/frontmatter).
- **Docs**: spec §2.0/§1/§8/§10 fold; SECURITY.md "Curate-time transcript
  distill" subsection.

**Focus areas.**
1. **D16 — distill never aborts a pass.** `_distill_one` wraps its body in a
   broad `except Exception` → fall back to skim. Is any failure path *outside*
   that guard (e.g. in `distill_docs`' loop, `dataclasses.replace`, the summary
   spread) able to bubble a distill error out of `curate()`? The invariant is:
   a distill failure NEVER changes the pass's success/abort outcome vs. skim-only.
2. **D17 — is every extracted value redacted before it's rendered?** Check
   `_render_claude` / `_tool_use_line` / `_result_text`: prompts, assistant text,
   `tool_result` bodies (`redact`), Bash commands (`redact_command`), the compact
   summary. Any transcript content that reaches the brain un-redacted is a
   blocker. The planted-secret test asserts the secret never reaches the brain
   input *or* the cache — is that assertion actually exercising the real path?
3. **Cache correctness (content-addressed).** `_source_fingerprint` hashes the
   raw body + transcript (path/size/mtime). Can a stale digest still be served —
   e.g. mtime granularity, a transcript edit that preserves size+mtime, or the
   dry-run/committed interaction? Is `_cache_read`'s tolerant `except ValueError`
   (treat unreadable cache as a miss) right?
4. **Provenance + consumption after body substitution.** The batch now folds a
   *digest* body under the raw's real filename. Confirm `from_raw` provenance and
   `mark_consumed(doc.file_path)` still target the real raw, and that
   `list_raw`/`list_curated` never see the substituted in-memory copies.
5. **Summary shape.** `distilled`/`fallback` keys were added to every post-distill
   summary (ok/error/dry-run/partial). Does any consumer — `status`, the
   `.curator-log.jsonl` reader, tests asserting summary equality — break on the
   new keys?

**Known risks / tradeoffs.**
- **Codex render is deliberately deferred** (ADR-0013 S-cf3): a Codex raw
  degrades to skim with no brain call. Spec §2.0 states this; there's a test. Not
  a bug — flag only if the *degradation* is wrong, not the absence of a Codex
  renderer.
- **`distill` mode is lenient:** only `"off"` disables; any other value
  (`"auto"`, typo, `"on"`) attempts distill. Deliberate (fail toward the
  documented default), but call it out if you think an unknown value should be
  rejected or warned.
- The digest-quality claim rests on S-cf5 (one substantive real session), not a
  corpus; the plan's live-eval "Done-when" note in `docs/notes/` is still open
  and I did **not** run a live `curate` against the real store (mutates the
  dogfood store + costs brain calls).

**How to verify.**
```
git diff cc36b78..HEAD
make ci                                   # 799 passed, ruff/format/mypy clean
uv run pytest tests/test_distill.py -v    # the D16/D17/cache matrix
# real render, no brain, against a live transcript (path is machine-specific):
uv run python -c "from pathlib import Path; from neurobase.curator import distill; \
p=next(Path.home().joinpath('.claude/projects').rglob('*.jsonl')); \
r=distill.render_transcript('claude', p, ()); print(len(r), 'chars'); \
print(distill.render_transcript('codex', p, ()))"   # codex ⇒ None (deferred)
```

**Out of scope.**
- ADR-0014 itself + the S-cf4/S-cf5 spikes — already approved (prior baton). Only
  flag an *implementation* that diverges from the approved contract.
- ADR-0012 batching internals (Accepted) — only the distill step layered above it.
- A live end-to-end brain eval / the `docs/notes/` comparison — tracked Phase C
  tail, not part of this diff.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** _pending._
