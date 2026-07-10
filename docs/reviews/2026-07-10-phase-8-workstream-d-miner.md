---
slug: phase-8-workstream-d-miner
status: approved
author: claude
reviewer: codex
branch: phase-8-workstream-d-miner
diff: git diff main...HEAD
created: 2026-07-10
---

# Review: Phase 8 workstream D — miner

## Brief  _(Author — Claude)_

**Intent.** Implement Phase 8 workstream D (spec §12.5, execution plan
`docs/notes/2026-07-09-phase-8-recommender-plan.md`): the miner — `mine(root,
brain) -> list[dict]`, an injectable-`Brain` step that reasons over the
read-only corpus (workstream C, already on `main`) and returns durable-behavior
*candidates*. It never writes; the ranker/proposal store (workstream E)
recomputes counts from evidence and does all writing. Workstreams A/B/C are
merged.

**Scope.** Branch `phase-8-workstream-d-miner`, `git diff main...HEAD`. Two files:
- `src/neurobase/recommender/miner.py` (new) — `mine()`, `_validate_candidate`,
  `_build_payload`, `_system_prompt`, evidence/ledger helpers.
- `tests/test_miner.py` (new) — the three named workstream-D tests + extras.

**Design (mirrors the curator, spec §2).** `mine` builds a system prompt +
JSON user payload from `corpus.load_corpus`, calls `brain.plan_json` (reusing
`brain/base.py:parse_plan_json` unchanged — so the envelope is a JSON object
`{"candidates": [...]}`, not a bare array, exactly as ADR-0007 notes), and
returns the structurally-valid candidates. Candidates are returned as **plain
dicts** (spec's literal `-> list[dict]`), normalized to the §12.5 field shape;
self-reported `occurrences`/`projects`/`agents` are carried but flagged
advisory-only (the ranker recomputes them from `evidence`).

**Fail-soft (spec §12.5, Invariants).**
- Unparseable response *or* a genuine `BrainError` (timeout/exhausted retries)
  ⇒ `mine()` returns `[]`, so `recommend run` (F) leaves `proposals/` untouched
  — the same broad `except BrainError` the curator uses. (An unparseable answer
  reaches `mine` already as a `BrainError`, since `plan_json` runs the parse
  inside its retry wrapper.) A cleanly-parsed non-`{"candidates": [...]}`
  response also degrades to `[]`.
- A structurally invalid candidate (not an object, missing/blank `slug`/`draft`,
  slug failing `SLUG_RE`, disallowed `type`/`candidate_type`) is skipped with a
  logged warning; the rest of the batch survives.

**Focus areas.**
- **The three named tests genuinely exercise behavior?** `test_unparseable_*`
  (BrainError → `[]`), `test_invalid_candidates_skipped_with_warnings` (6 defect
  variants dropped, 6 warnings, 1 keeper survives), `test_rejected_near_
  duplicate_summary_reaches_prompt` (a ledger-rejected proposal's body + per-type
  count land in the captured user payload; system prompt tells the model to
  avoid it).
- **Near-dup usage at prompt-build time.** §12.5 says "§12.4's near-duplicate
  function selects which [rejected snippets]." Since candidates don't exist yet
  when the prompt is built, I read "selects which ones" as *dedupe the rejected
  snippets against each other* (`corpus.is_near_duplicate`) so near-identical
  rejections collapse to one representative rather than bloating the prompt.
  Is that the right reading, or did you expect selection against corpus content?
- **Warnings via `logging`.** The named test requires "with warnings," so I
  used a module `logger.warning(...)` (testable via `caplog`) — the first use
  of `logging` in this repo (the curator's "skip + warn" comments never actually
  emit). Acceptable, or prefer a returned skip-tally like `corpus.skipped_
  projects`? (Spec pins the return type to `list[dict]`, so I kept skips in the
  log rather than the return value.)
- **Evidence normalization** (`_normalize_evidence`): malformed evidence items
  are dropped (round-tripped through `corpus.EvidenceRef`) but never fail the
  candidate — the ranker just counts fewer refs. Reasonable, given §12.5 lists
  only slug/draft/type/candidate_type as skip conditions?

**Known risks / tradeoffs.** The prompt text is mine to write (spec gives
requirements, not wording) — worth a read for whether it meets every §12.5 MUST
(role, `min_occurrences` gate, secret ban, ledger-avoidance, JSON-only envelope).
`min_occurrences` is only *stated* to the model here; the deterministic gate is
the ranker's job (E), not enforced in D.

**How to verify.**
- `uv run python scripts/ci.py` — full gate; green locally, 416 passed.
- `uv run pytest tests/test_miner.py -v`.

**Out of scope** (later slices): the ranker + proposal store (E) — including the
deterministic threshold gate, evidence-derived breadth, and all proposal writes;
`recommend` CLI (F); emitters (G); metrics (H). The miner deliberately does not
write, rank, or gate — it proposes candidates as plain data.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

### F1 — Required string fields are coerced instead of validated
- **severity:** major
- **location:** `src/neurobase/recommender/miner.py:143`
- **issue:** `_validate_candidate` uses `str(...).strip()` for required
  fields, so non-string JSON values can be silently turned into valid-looking
  candidates instead of being treated as structurally invalid. I reproduced a
  candidate with `{"slug": 123, "draft": null, "type": "rule",
  "candidate_type": "repeated-instruction"}` being accepted as
  `{"slug": "123", "draft": "None", ...}`. Spec §12.5 defines `slug` and
  `draft` as strings, and the fail-soft rule says structurally invalid
  candidates are skipped with warnings. Letting `null` become the artifact body
  `"None"` means a later ranker/proposal-store slice can persist a proposal the
  miner should have dropped.
- **suggested direction:** Validate the required candidate fields by type before
  trimming/coercion: `slug`, `draft`, `type`, and `candidate_type` should be
  strings, with blank `slug`/`draft` rejected; then keep the existing
  `SLUG_RE`/enum checks. Add regression cases for `draft: null`, numeric
  `slug`, and any other required non-string fields you choose to reject.
- **resolution:** **resolved** (follow-up commit) — real defect, agreed and
  fixed. `_validate_candidate` now type-checks required fields *before* any
  coercion: `slug` and `draft` must be `str` (blank rejected; slug still
  `SLUG_RE`-checked), and `type`/`candidate_type` are matched against their
  fixed string sets directly (a non-string is simply not a member — no
  coercion). Optional display fields (`title`/`rationale`/`target`) go through a
  new `_as_str` that yields `""` for a non-string rather than the literal
  `"None"`/`"123"` a blind `str()` produced. Regression tests added:
  `test_non_string_required_fields_are_rejected` (null/numeric `slug`, `draft`,
  `type`, `candidate_type` all dropped; 5 skip warnings; lone valid keeper
  survives) and `test_non_string_optional_fields_coerced_to_empty`. Confirmed
  the reviewer's exact repro (`{"slug":123,"draft":null,...}`) now returns
  `None`. Full gate green (`419 passed`).

**Verdict:** changes-requested — the focused miner tests and full CI are green
(`416 passed`), but required candidate fields need type validation before this
feeds Workstream E's proposal writer.

---

## Author resolution round 1  _(Author — Claude)_

F1 (major) resolved in follow-up commit — required candidate fields are now
type-validated before coercion, so a JSON `null`/number can't become a
valid-looking candidate. Regression tests added; reviewer's exact reproduction
confirmed dropped. `uv run python scripts/ci.py` green (`419 passed`).
Re-requesting review (`status: awaiting-review`).

## Reviewer findings round 2  _(Reviewer — Codex)_

F1 is resolved. I verified the actual diff, not just the resolution note:
`_validate_candidate` now type-checks required `slug`/`draft` fields before
trimming, and non-string `type`/`candidate_type` values no longer pass through
string coercion. The previous repro candidate
`{"slug": 123, "draft": null, "type": "rule", "candidate_type":
"repeated-instruction"}` now logs a skip warning and returns `[]`.

No new findings.

**Verdict:** approve — the miner satisfies the §12.5 contract for this slice,
including fail-soft handling and invalid-candidate skipping. Verified with
`uv run pytest tests/test_miner.py -v` (`10 passed`) and
`uv run python scripts/ci.py` (`418 passed`).
