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

_(awaiting review)_

**Verdict:** _pending._
