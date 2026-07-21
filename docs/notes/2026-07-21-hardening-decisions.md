<!-- markdownlint-disable -->
> **Filed 2026-07-21 — what this is:** the maintainer's ratified A/B decisions on
> every issue in the [hardening plan draft](2026-07-21-hardening-plan-draft.md),
> made issue-by-issue in session. Like the plan itself this note carries **no
> authority** — it is not a contract (see [README](README.md)); the
> [spec appendix](../neurobase-spec-appendix.md) and [ADRs](../adr/README.md)
> remain the law. It is the **input to the Phase-0-onward ADRs**: each decision
> below is meant to be promoted into an ADR (as §15 already became
> [ADR-0015](../adr/0015-store-chokepoint-handle.md)) before any code lands.
> Section numbers refer to the plan draft.

---

# Hardening plan — ratified decisions (2026-07-21)

## Decision table

| Issue | Plan § | Decision |
|---|---|---|
| **9 — schema validation** | §15 | **DONE — ADR-0015.** Validated `StoreHandle` / `open_store()` (Solution A). Not yet implemented. |
| **3 — sensitive egress** | §9 | **Solution B (egress policy) adopted P0.** DLP (Sol A) = **interface-only**, no bundled model. Privacy modes shipping: **`local-only` only**. |
| **4 — invisible hook failure** | §10 | **Receipts + deterministic health checks** (Sol A core). **AI diagnosis (`doctor --explain`) shipped now**, egress-gated. Surfacing: **`status` freshness/degraded only** (no nonzero `doctor` exit, no injected warning, no OS notification). |
| **1 — curator preserves wrong facts** | §7 | **Both** — independent verifier (A) **and** candidate→active→disputed→tombstoned lifecycle (B). **Every model upsert → candidate** (`memory_remember` stays active+pinned). Verifier model **preferred-distinct, not required**. |
| **6 — cross-project boundaries** | §12 | Profiles = **logical partitions under one root** (`StoreHandle` carries a profile qualifier). Cross-project mining **explicit-only**. **AI scope/sensitivity classifier shipped now**, egress-gated, restrictive defaults on low confidence. |
| **2 — recall not relevance-based** | §8 | Deterministic weighted retrieval (B) is the base. **AI reranker mid-session/MCP only** — startup stays deterministic + non-blocking. **Reserved budget floor** for current decisions/blockers/safety constraints. |
| **8 — lexical search/dedup** | §14 | **SQLite FTS5 derived index adopted** (rebuildable; Markdown stays truth). Also backs Issue 2 candidate generation + recommender dedup. **Embeddings flagged now**, off by default, egress-gated (local model / disabled for `local-only`). |
| **5 — promotes mistakes/workarounds** | §11 | **Both** — adversarial critic (A, egress-gated) **and** deterministic durability/trial gates (B). **Trials always require explicit user confirmation** to go permanent. |
| **7 — metrics measure proxies** | §13 | Rename proxies + observed-outcome metrics (B). **Git commit survival** = the one authoritative cross-agent success signal, kept **advisory** (`require_success_signal` non-blocking; human confirmation is the gate). **Paired-replay + blinded-judge harness built now**, explicit-command-only (`recommend evaluate`). |
| **10 — narrow agent/monorepo/sync** | §16 | **Defer** adapter SDK + generic MCP mode until core is proven (§30). **Fold monorepo `match_subpath` into the registry redesign now.** **Bake in conflict-safe sync primitives now** (stable ULIDs, append-only ledger, content-hash conflict detection); actual multi-machine sync stays documentation-only. |

## §27 "review decisions required" — resolution

| Q | Question | Answer |
|---|---|---|
| Q1 | Candidate default for every upsert or only low-confidence? | **Every model upsert → candidate.** |
| Q2 | Distinct verifier model required? | **Preferred, not required** (`require_distinct_backend = false`). |
| Q3 | Which privacy modes ship first? | **`local-only` only.** |
| Q4 | DLP bundled or extension point? | **Extension point** (interface-only). |
| Q5 | Profiles physical or logical? | **Logical partitions, one root.** |
| Q6 | Startup AI reranking acceptable? | **No — mid-session/MCP only** for now; startup deterministic. |
| Q7 | Cross-agent success signals? | **Git commit survival** (deterministic, agent-independent). |
| Q8 | Trials auto-promote? | **No — always require user confirmation.** |
| Q9 | FTS5 acceptable as derived core infra? | **Yes** (derived, rebuildable). |
| Q10 | Multi-machine: product vs docs? | **Conflict-safe primitives baked in now; sync stays documentation-only.** |

## Cross-cutting consequences for the ADRs

- **Registry → project records = a `store.toml` schema-2 change (§25).** One migration
  should carry: `profile` (logical), `privacy` mode (`local-only` + a defined default
  that still routes through the gate and allows currently-configured backends),
  `allowed_brains`, `allow_*` flags, **`match_subpath`** (monorepo), **ULID** event
  ids, **append-only** ledger semantics, **content-hash** conflict detection. Fold
  Issue 3/6/10's schema needs into this single migration, not several.
- **`StoreHandle` (ADR-0015) gains a profile qualifier** — pin this before migration
  step 1 code, since it changes the `open_store()` signature.
- **The central egress gate goes live carrying a lot of purposes from early on.**
  Shipping-now AI calls that all route through `authorize_egress()`:
  curator **verifier**, scope **classifier**, health **diagnosis**, recommender
  **critic**, evaluation **judge**, mid-session **reranker**, **embeddings**. Deferred
  behind an interface only: **DLP**. This is a more AI-forward scope than the plan's
  most conservative reading — deliberate, and the gate is the single control point.
- **Config deltas from §19 defaults:** `[curate.verify] enabled = true`; candidate
  facts default + `[facts] inject_candidates = false`; `[inject]` startup rerank off
  (mid-session only); `[search] backend = "fts5"`, embeddings behind a flag;
  `[recommend] critic = "on"`, trials require confirmation, `require_success_signal`
  advisory; `[privacy.dlp] enabled = false` (interface only).

## Phase mapping (confirms §26, with Issue-10 folds pulled early)

- **Phase 0** — `StoreHandle` (ADR-0015); egress policy + logical profiles (`local-only`);
  hook receipts + `status` surfacing + egress-gated `doctor --explain`. *Schema-2
  registry redesign carries the `match_subpath` + ULID/append-only/content-hash folds.*
- **Phase 1** — candidate lifecycle + `facts` CLI + independent verifier; scope/sensitivity classifier.
- **Phase 2** — FTS5 index; deterministic weighted retrieval + reserved budget floor; mid-session reranker; embeddings flag.
- **Phase 3** — critic + durability/trial gates; renamed + observed-outcome metrics; `recommend evaluate` replay harness.
- **Phase 4/5** — adapter SDK + generic MCP mode **deferred**; multi-machine sync documentation-only (primitives already in place from Phase 0).
