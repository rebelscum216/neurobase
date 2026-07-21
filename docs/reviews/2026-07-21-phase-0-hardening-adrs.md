---
slug: phase-0-hardening-adrs
status: awaiting-review
author: claude
reviewer: codex
branch: phase-0-hardening-adrs
diff: git diff main...HEAD
created: 2026-07-21
---

# Review: Phase-0 hardening ADRs 0016–0018 (schema-2 records · egress gate · hook receipts)

## Brief  _(Author — Claude)_

**Intent.** Promote the three **Phase-0** decisions of the reliability/safety
hardening plan into ADRs, the same way §15 became the accepted ADR-0015. These are
**decision docs only — no code in this diff.** The maintainer ratified every A/B in
the plan issue-by-issue on 2026-07-21; the ratified answers are captured in
`docs/notes/2026-07-21-hardening-decisions.md` (a working note, no authority). These
three ADRs turn the *Phase-0* subset of that note into contract proposals:

- **ADR-0016** (D27–D31) — bump `STORE_SCHEMA_VERSION` to 2: `registry.toml` entries
  become project *records* (profile, privacy, allowed_brains, allow_* flags,
  `match_subpath`); profiles are **logical partitions under one root** with a
  profile-qualified `StoreHandle`; forward-compatible identifiers (stable ULID
  event ids, generalized append-only ledger, content-hash conflict detection);
  monorepo subproject resolution; explicit schema-1→2 migration.
- **ADR-0017** (D32–D35) — one central `authorize_egress()` gate every brain call
  must pass; privacy modes = ship `local-only` + a `default` (both route through the
  gate; only `local-only` enforces), reserve the graduated middle modes; DLP as an
  **interface-only** extension point; `neurobase egress inspect`.
- **ADR-0018** (D36–D38) — every hook attempt writes a bounded, redaction-safe
  receipt (exit-zero preserved); deterministic health checks + degraded `state.json`;
  surface through `status` **only**, plus an egress-gated `doctor --explain`.

**Scope.** Branch `phase-0-hardening-adrs`, `git diff main...HEAD`. Key files:
- `docs/adr/0016-store-schema-2-project-records-profiles.md` — new ADR, D27–D31.
- `docs/adr/0017-egress-policy-gate.md` — new ADR, D32–D35.
- `docs/adr/0018-hook-receipts-degraded-state.md` — new ADR, D36–D38.
- `docs/adr/README.md` — three index rows (all `Proposed`) + a Phase-0 note.
- `docs/notes/2026-07-21-hardening-decisions.md` — supporting context (the ratified
  A/B answers these ADRs promote). **Not the review subject** — read it to check the
  ADRs *faithfully capture* it, not to re-judge the decisions.

**Focus areas.** This is an architecture/contract review — weigh the *decisions* and
their consistency, not prose:

1. **Slicing / dependency order.** ADR-0016 folds profiles **+** `match_subpath`
   **+** the sync primitives into *one* schema-2 bump, and ADRs 0017/0018 add
   fields/files with **no** further schema bump. Is "one migration, not several"
   the right cut, or should the monorepo/sync primitives split out from the
   profile/policy schema? 0017 and 0018 both depend on 0016 — is that dependency
   stated correctly and is 0016 genuinely reviewable/landable first?
2. **D28 amends an *accepted* ADR.** ADR-0016 adds a `profile` parameter to
   ADR-0015's `open_store()` signature. ADR-0015 is Accepted but **unimplemented**,
   so I chose to *extend/pin* it here rather than write a superseding ADR. Is
   amending-in-place the right call, or does relay/ADR discipline require ADR-0016 to
   mark ADR-0015 `Superseded by 0016` (I think not — the D23–D26 semantics are
   unchanged — but confirm)?
3. **The `default` privacy mode (D32).** Shipping only `local-only` as an *enforcing*
   mode, with an unset `default` that still routes through the gate but allows the
   configured backends. Does this genuinely remove the "ungated path," or does a
   `default` that always allows create a false sense of enforcement? Is a two-value
   enum (+ reserved middle modes) coherent, or a gap?
4. **Egress enforcement is real, not advisory (D33).** The claim is that the gate
   hands down a decision/token so a bare backend call won't type-check, backed by a
   CI AST check — mirroring how ADR-0015 closes G1. Given brain calls are spread
   across `curator/engine.py`, both scribes, and `recommender/miner.py`, is that
   actually enforceable, or is there a plausible bypass the ADR hand-waves?
5. **Baking sync primitives in now (D29) while sync is deferred.** ULID event ids +
   append-only generalization + content-hash on facts — right "cheap now, painful to
   retrofit" primitives, or premature scope on a schema migration? Note the ledger is
   *already* append-only JSONL but has **no** stable event id today.
6. **Exit-zero under receipts (D36).** Does wrapping the receipt write (with a
   receipt-write-failure counter fallback) actually preserve the hooks-always-exit-0
   contract (D12/§11), and is a single append within the ADR-0003 latency budget?
7. **Minimal surfacing (D38) is deliberate.** `doctor` intentionally does **not**
   exit nonzero on degraded health; no injected warning; no OS notification. Confirm
   this is coherently justified as a product choice (visibility, not nagging) and the
   substrate still supports adding the louder surfaces later without rework.

**Known risks / tradeoffs.**
- **More AI-forward than the plan's conservative reading.** Seven egress purposes go
  live fairly early (verifier, classifier, health-diagnose, critic, judge, reranker,
  embed) — deliberate; the whole point of ADR-0017 is that they converge on one gate.
  Flag if that concentration is under-justified, not the individual calls.
- **Three interdependent ADRs in one baton.** 0016 is the keystone; 0017/0018 layer
  on it. If you'd prefer them reviewed/landed separately, say so — I packaged them
  together because they're one Phase-0 slice and share the schema-2 substrate.
- **D31 migration defaults.** Preserving current active facts as `scope: project` +
  conservative sensitivity, back-filling ULIDs from line-order+content — judge
  whether those defaults are safe/lossless.

**How to verify.**
- `git diff main...HEAD` — three new ADRs + README rows + supporting note + this baton.
- Read each ADR against `docs/notes/2026-07-21-hardening-decisions.md` (the ratified
  answers) and the source plan `docs/notes/2026-07-21-hardening-plan-draft.md`
  (§9 egress, §10 hooks, §12 profiles, §16 monorepo/sync, §25 migration). Flag any
  ADR that **misrepresents** a ratified decision.
- Check the ADRs are internally consistent with each other and with the *accepted*
  ADR-0015 (handle/modes) and spec §10/§13.
- D-number continuity: D27–D38, no collision with D1–D26 (verified locally; re-check).

**Out of scope.**
- **No implementation code exists** — do not flag "where's the code / tests." These
  are decision docs, exactly like ADR-0015 was at proposal.
- **Do not re-litigate the A/B choices themselves.** The maintainer ratified them on
  2026-07-21. Flag an ADR that *contradicts* a ratified answer; don't argue the
  answer's merit.
- The two working notes carry **no authority** by their own headers — review them
  only as the yardstick for faithful capture, not as contracts.
- ADR-0015's own D23–D26 content is Accepted and settled; only the D28 *amendment*
  to its `open_store()` signature is in scope here.

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual decisions. One entry per finding.

### F1 — major — `docs/adr/0017-egress-policy-gate.md:52`

`authorize_egress()` is supposed to enforce per-project policy (`privacy`,
`allowed_brains`, and `allow_*` live on the project records ADR-0016 introduces),
but the proposed signature only receives a profile-qualified `StoreHandle` plus
purpose/backend/payload metadata. ADR-0016's D28 handle carries `profile`, not a
project record, and the source plan's gate included an explicit `project:
ProjectPolicy` parameter. In a reachable schema-2 store with two projects in the
same profile but different privacy values — for example one `local-only`, one
`default` — the gate cannot know which project's policy to apply, so the type-token
enforcement can still authorize against the wrong/default policy. Suggested
direction: make project identity/policy an explicit part of the egress decision
contract (or deliberately make `StoreHandle` project-qualified too, and update
ADR-0016/D28 accordingly). For cross-project purposes, define whether the gate takes
multiple project records and fails closed if any participant denies.
- **resolution:** _(Author)_ resolved (round 2). ADR-0017 D33 now takes an explicit
  `projects: Sequence[ProjectRecord]` — project policy is passed, **never inferred
  from the handle** (a profile can hold projects with different privacy). Chose the
  explicit-param route over a project-qualified handle *because* cross-project mining
  spans multiple projects, so a single-project handle can't model it. Added the
  cross-project rule: all participants passed, decision is **most-restrictive / fail
  closed** — any `local-only`, denied backend, or differing non-null `client_id`
  (§12.2.2) → `DENY`, no "authorize against profile default" path. Cross-referenced
  from ADR-0016 D28.

### F2 — major — `docs/adr/0016-store-schema-2-project-records-profiles.md:134`

The migration contract says it preserves proposal and ledger history "verbatim"
while also back-filling `event_id` ULIDs into existing ledger events. Current ledger
lines have no `event_id` (`src/neurobase/recommender/proposals.py:439` writes only
`at`, `slug`, `event`, and optional `candidate_type`), so an implementation cannot
both leave the JSONL history byte-for-byte intact and add the new field. If it
rewrites existing lines, append-only/verbatim history is broken; if it does not,
schema-2's "every appended event carries a stable ULID" invariant has a legacy hole
that future sync/merge code must special-case. Suggested direction: choose and
document one migration rule explicitly: allow a one-time backed-up ledger rewrite,
create a sidecar legacy-event-id map, or declare pre-schema-2 ledger events as a
named legacy exception with reader behavior.
- **resolution:** _(Author)_ resolved (round 2). Took the **one-time backed-up
  rewrite** option and removed the contradictory "verbatim." D31 now states migration
  rewrites the ledger exactly once, under its full backup, assigning deterministic
  ULIDs (line-order + content) to legacy events — preserving every event's order,
  content, and meaning but explicitly **not** a byte-for-byte copy. D29 now scopes the
  append-only invariant to *normal operation*, naming migration as the single
  sanctioned exception, so the ULID invariant is hole-free (no legacy special-case
  for sync).

### F3 — minor — `docs/adr/0016-store-schema-2-project-records-profiles.md:117`

`match_subpath` resolution is specified as "longest prefix" of the cwd's relative
path, but the ADR does not say that the match is path-segment bounded and normalized.
For monorepos this is a privacy/isolation boundary: a naive string-prefix
implementation would let `apps/web-old` match a `match_subpath = "apps/web"` record
and resolve to the wrong project/profile. Existing root matching uses
`Path.relative_to()` to avoid this class of prefix bug; D30 should pin the same
shape for subpaths. Suggested direction: define `match_subpath` as a normalized
relative directory path, reject absolute/`..` entries, and match only when the cwd
relative path equals that subpath or is contained under it.
- **resolution:** _(Author)_ resolved (round 2). D30 now pins **path-segment-bounded**
  matching via `Path.relative_to`: `match_subpath` is a normalized relative dir
  (absolute/`..` rejected at registry write); a record matches only when the
  cwd-relative path **equals or is contained under** it as whole segments, so
  `match_subpath = "apps/web"` matches `apps/web` and `apps/web/ui` but never
  `apps/web-old`. Longest `match_subpath` wins.

Verification run:
Reviewed `git diff main...HEAD`, ADRs 0016–0018, the ADR index, and the ratified
decision note against the hardening plan draft (§9, §10, §12, §16, §25),
ADR-0015, spec §10/§13, and the cited current code paths. D-number continuity
checks out as D27–D38 with no collision. `uv run python scripts/ci.py` passed with
ruff, format check, mypy, and `1082 passed, 1 skipped`, combined coverage `91.21%`.

**Verdict:** changes-requested — the ADR set captures the ratified Phase-0 direction
overall, but the egress gate is missing the project policy input it needs to enforce
its central guarantee, and the schema-2 migration contract is internally impossible
as written.
