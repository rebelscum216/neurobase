---
slug: capture-fidelity-foundation
status: awaiting-review
author: claude
reviewer: codex
branch: capture-fidelity-foundation
diff: git diff main...HEAD
created: 2026-07-14
---

# Review: capture-fidelity foundation — curator plan batching + Tier-1 richer skim

## Brief  _(Author — Claude)_

**Intent.** Two changes that land together, from the capture-fidelity plan
(Phases A + B; Tier 2 distill and Tier 3 substrate copy are **not** here).

1. **Curator plan batching (prerequisite, ADR-0012 / D22).** `curate()` sent
   every unconsumed raw plus every active fact in one `brain.plan_json` call with
   no size guard. CLI brains pass system+user as a single argv entry, so a
   backlog — or any richer capture — eventually fails before the model is even
   reached. Plan requests are now capped by the **UTF-8 byte length of the exact
   combined prompt** (default 262,144 = 256 KiB, `[curate].plan_payload_max_bytes`).
   Raws plan oldest-first in sequential batches; each batch applies and consumes
   before the next, reloading facts so later batches see earlier upserts,
   supersessions, and tombstones. Node synthesis, prune, index, and linkify run
   once after the last batch.
2. **Tier-1 richer skim (ADR-0013).** Fixes the four documented loss modes:
   L1 the final-message trap (summary = longest of the last 3 assistant texts,
   not last-non-empty), L2 vanished subagent reports (`Agent`/`Task` tool-use ids
   correlated to their `tool_result`), L3 invisible tool activity (files touched
   / commands run), L4 the 600-char prompt cap (now 1,200).

**Scope.** Branch `capture-fidelity-foundation`, `git diff main...HEAD` (one
commit, `103c9fc`). Key files:

- `src/neurobase/curator/engine.py` — the batch loop, byte budgeting
  (`_plan_request_bytes`, `_next_plan_batch`, `_truncate_raw_to_fit`).
- `src/neurobase/adapters/scribe_common.py` — **new.** The agent-agnostic §8
  assistant bounds and the three helpers both scribes share: `bounded_highlights`,
  `final_summary`, `bullet`.
- `src/neurobase/adapters/claude/scribe.py` — highlights, subagent reports,
  activity digest, compact summaries as highlights.
- `src/neurobase/adapters/codex/scribe.py` — highlights + summary fix + prompt cap
  (items 1, 2, 5 of the plan's A1; activity/subagent parity deferred per ADR-0013).
- `src/neurobase/core/config.py`, `cli/__init__.py` — the new config key; `--dry-run`
  prints `plans` when the backlog needed more than one batch.
- `docs/neurobase-spec-appendix.md` §2 / §4 / §5 / §8 / §10 / §11.1, `docs/adr/0012`,
  `docs/adr/0013`, `docs/adapter-guide.md`, `docs/how-it-works.md`.

**Policy choices the user approved** (so these are settled, not open questions):
prompts 1,200 chars · highlights 500 each / 6,000 total · subagent reports
5 × 1,500 · request cap 256 KiB measured on the final UTF-8 request · earlier
curator batches stay committed when a later batch fails · Claude compact summaries
are highlights, not prompts or final summaries.

**Focus areas.**

- **The D9 refinement is the one contract change worth your hardest look.** v0.1:
  "a plan that won't parse ⇒ leave *every* raw unconsumed." Now: a failed batch and
  all later batches stay unconsumed, but earlier committed batches stand (D22). A
  first-batch failure is byte-for-byte the old behavior, and the single-batch case
  (essentially all real passes) is unchanged — `test_single_batch_payload_is_v01_byte_identical`
  pins that the payload string is identical to v0.1's. But the invariant genuinely
  weakened from "all-or-nothing" to "no raw is consumed without a valid plan that
  saw it." Is that the right line? Spec §2 and its partial-failure contract are
  rewritten to say so explicitly.
- **Byte budgeting.** `_plan_request_bytes` measures `combine_prompt(PLAN_SYSTEM, user)`
  encoded UTF-8 — the exact argv string a CLI brain sends. The API backend has no
  argv limit but is budgeted the same way (deliberately conservative). Is measuring
  the *combined* shape for every backend right, and is `_truncate_raw_to_fit`'s
  binary search sound? It searches character prefixes while measuring serialized
  bytes, which is monotonic but not proportional.
- **Curated facts grow across batches.** Each batch carries the full fact list. On a
  huge backlog the facts could crowd out the raw budget; the pass then errors with
  those raws unconsumed rather than looping. Acceptable failure mode?

**Known risks / tradeoffs.**

- **Dry-run on a multi-batch backlog is N independent previews, not a simulation.**
  Every preview batch plans against the *current* persisted facts, because a dry run
  applies nothing. So dry-run batch 2 differs from what a real run's batch 2 would
  send (which would see batch 1's writes). I chose honesty over a fake in-memory
  simulation; the shape is documented. Flag it if you disagree.
- **`_next_plan_batch` re-serializes the payload per candidate raw** — O(n²) JSON
  work in the number of raws in a batch. Bounded by the 256 KiB budget and curate is
  not latency-sensitive, so I left it simple rather than incrementally sizing.
- **Codex has no activity or subagent sections.** ADR-0013 defers both: `response_item`
  command/patch strings need their own bounded format contract first. The two scribes'
  bodies are therefore asymmetric by design.

**What I changed in Codex's implementation** (I adopted it, but did not assume it
was correct because its tests passed — the four below are mine, on top of the
foundation as handed over):

1. **Crash → lost capture.** `command.splitlines()[0]` raised `IndexError` on an
   empty-string Bash command (`"".splitlines() == []`), and the hook wrapper's
   exit-0 guarantee would have turned that into *the whole session captured
   nothing*. The plan calls this path "best-effort — never fatal." Guarded, with
   `test_activity_digest_survives_odd_and_empty_tool_inputs`.
2. **Unbounded activity scan.** `path not in activity_files` deduped against a list
   that grew without limit and was only capped at return, so a session touching
   thousands of files did O(n²) work inside the hook latency budget. Now capped at
   collection — same output, bounded work.
3. **Duplicated eviction logic.** The highlight bounds and their newest-first
   eviction were written twice, once per scribe, for one spec-§8 contract that is
   explicitly agent-agnostic. Extracted to `adapters/scribe_common.py`, mirroring the
   existing `recall_common.py` precedent; both scribes re-export via `__all__`.
4. **Bullets could forge section headings — found by the live spike, not by tests.**
   Bullets rendered as `- {text}`, so the *second* line of a multi-line prompt or
   highlight landed at column 0. Running the new scribe over my largest real
   transcript produced a raw with **seven forged `##` sections** — including a
   content-supplied `## Final assistant summary` — in the document the curator then
   reads as structure. Raising prompts to 1,200 chars and adding 1,500-char subagent
   reports made multi-line content the common case rather than an edge one. All
   bullets now indent continuation lines (`scribe_common.bullet`); spec §4's body
   format and ADR-0013 record the rule; `test_multiline_content_cannot_forge_a_section_heading`
   pins it.

**How to verify.**

```bash
git diff main...HEAD
uv run python scripts/ci.py          # full gate: 497 passed, ruff/format/mypy clean
```

Evidence I gathered rather than inherited:

- **Argv boundary (S-cf4, ADR-0012).** Re-measured independently: `ARG_MAX` 1,048,576;
  largest single argv arg accepted 1,045,140 bytes, 1,045,141 fails. ADR-0012 recorded
  1,045,268/1,045,269 — the delta is environment size (2,804 bytes here vs 2,485 then),
  which is exactly the ADR's point that the boundary *moves with the environment* and a
  character cap can't track it. The 256 KiB default is 25% of the measured ceiling.
- **Hook latency (ADR-0003 follow-up).** Ten parses of the largest local Claude
  transcript (8,924,926 bytes): avg **51.0 ms**, peak **59.1 ms** against the 500 ms
  budget. ADR-0013 originally recorded 38.8/42.9 ms; I could not reproduce those exact
  numbers and have corrected the ADR to carry both runs and name the finding as the
  order-of-magnitude headroom rather than either point estimate.
- **Live end-to-end spike.** Real transcript → real scribe → real store (throwaway root):
  writes a 20,434-byte raw carrying `## Session` / `## Prompts` / `## Activity` /
  `## Assistant highlights` / `## Final assistant summary` and nothing else. That is
  7.7% of one 256 KiB plan request, so Tier-1 capture does not threaten the ADR-0012
  budget at realistic session sizes. This spike is what caught finding 4 above.

**Assumptions / residual concerns** (not blockers, but you should know):

- **The `## Final assistant summary` body is still emitted unbulleted**, so content
  inside it can still contain a `##` line. It is the last section, so nothing can be
  shadowed by it, and this is v0.1 behavior I did not change — but it is the one
  remaining place session content reaches column 0.
- **Redaction runs over the assembled body, after the new sections are built**, so
  highlights/reports/activity are covered by D13 — but the new sections widen *what*
  gets captured, and tool-derived text (file paths, command lines) is a category the
  D13 table was not designed against. No secret shapes appeared in the live spike, but
  a fresh look at the regex table against command lines would be worth it. I did not
  touch SECURITY.md; Tier 2's transcript access is where that note belongs.
- **Batching's "oldest-first" leans entirely on `store.list_raw`**, which the curator
  does not re-sort. I checked: `core/store.py:220` guarantees it (the ISO-8601 filename
  prefix sorts chronologically under `sorted()`). Noting it because batching now depends
  on that ordering being a *contract*, where before it was merely convenient.
- **I did not re-run the S-cf1/S-cf2/S-cf3 shape spikes.** I verified ADR-0013's claims
  structurally against my own local transcripts (Agent/tool_result correlation and
  compact-summary shape both hold), but the Codex `response_item` conclusion is
  inherited, not independently confirmed.

**Out of scope.**

- Tier 2 (transcript distill, `transcript_path` frontmatter, digest cache) and Tier 3
  (substrate copy). Neither is started; the plan's Phase C/D.
- Codex activity/subagent parity (ADR-0013 defers it explicitly).
- `docs/notes/2026-07-09-phase-8-recommender-plan.md` is modified in the working tree
  but is **user-owned and deliberately not in this commit** — do not review it.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

_(none yet)_

**Verdict:** _pending_
