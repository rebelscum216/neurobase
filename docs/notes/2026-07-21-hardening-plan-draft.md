<!-- markdownlint-disable -->
> **Filed 2026-07-21 — what this is:** the maintainer's reliability / safety /
> retrieval / evaluation hardening plan, captured here **verbatim** as a working
> note. Like every note it carries **no authority** — it is not a contract (see
> [README](README.md)); the [spec appendix](../neurobase-spec-appendix.md) and
> [ADRs](../adr/README.md) remain the law. It is the **source roadmap for Phase 0
> onward**. The first decision promoted from it is
> [ADR-0015](../adr/0015-store-chokepoint-handle.md) (store chokepoint /
> `StoreHandle` — the G1 fix, this plan's §15). Its section numbers are preserved
> unchanged so the next Phase-0 ADRs (profiles, egress policy, hook receipts) can
> cite them. Content below is the draft as received; only this header was added.

---

# Neurobase Reliability, Safety, Retrieval, and Evaluation Hardening Plan

**Status:** Draft for architecture and implementation review  
**Repository:** `rebelscum216/neurobase`  
**Baseline:** `main`, reviewed July 20, 2026  
**Audience:** Maintainers, reviewers, security reviewers, and contributors  
**Primary objective:** Preserve Neurobase's local-first, fail-soft, Markdown-as-truth design while adding stronger controls for memory correctness, privacy, relevance, observability, recommendation quality, and measurable outcomes.

---

## 1. Executive summary

Neurobase already has a strong mechanical foundation: deterministic capture, redaction before writes, a bounded curator, Markdown-backed storage, automatic cross-agent recall, MCP tools, consent-first artifact installation, and a human-reviewed recommender. The main remaining risk is not whether information can move through the system. It is whether the information being curated, recalled, generalized, and promoted is correct, relevant, appropriately scoped, and demonstrably useful.

This plan hardens Neurobase around ten issues:

1. A structurally valid curator plan can still preserve an incorrect fact.
2. Automatic recall is ordered alphabetically rather than by task relevance.
3. Pattern-based redaction does not fully control sensitive-data egress to model backends.
4. Fail-soft hooks can fail invisibly for long periods.
5. Repetition can cause the recommender to promote mistakes or temporary workarounds.
6. Cross-project mining can cross privacy and applicability boundaries.
7. Current recommender metrics are operational proxies, not outcome measurements.
8. Search and duplicate detection are mostly lexical.
9. Store-schema validation is enforced inconsistently across entry points.
10. Agent, platform, monorepo, team, and multi-machine support is intentionally narrow.

The recommended architecture uses AI calls where judgment is genuinely valuable—verification, classification, reranking, criticism, evaluation, and diagnostics—but keeps deterministic code responsible for authorization, scope enforcement, schema safety, thresholds, file writes, migrations, and final state transitions.

---

## 2. Goals

### 2.1 Product goals

- Prevent one misunderstood session from becoming durable, self-reinforcing memory.
- Select startup context by relevance rather than filename order.
- Make model egress visible, configurable, and enforceable per project or profile.
- Preserve hooks' exit-zero contract while making failures observable.
- Distinguish durable practices from repeated failures, incident-specific workarounds, and temporary constraints.
- Prevent confidential or project-specific knowledge from being generalized across inappropriate boundaries.
- Measure whether accepted skills and rules improve coding outcomes.
- Improve semantic recall without abandoning Markdown as the source of truth.
- Make schema compatibility impossible to forget at a new call site.
- Create a clean path for additional agents, monorepo subprojects, and optional synchronization.

### 2.2 Engineering goals

- Keep all AI interfaces injectable and testable with fakes.
- Bound every additional model call by count, payload size, latency, and fallback behavior.
- Require structured model output and validate it before use.
- Store enough provenance to audit every fact, recommendation, and decision.
- Fail closed on privacy and authorization decisions.
- Fail soft on agent lifecycle hooks.
- Keep derived indexes disposable and rebuildable.
- Ensure every new safety property has deterministic tests.

---

## 3. Non-goals

- Replacing Markdown with a database as the source of truth.
- Allowing a model to make authorization or privacy-boundary decisions without deterministic enforcement.
- Automatically installing skills or rules without review.
- Turning hooks into blocking, failure-prone orchestration pipelines.
- Sending raw, unredacted transcripts to a remote service.
- Requiring a hosted Neurobase control plane.
- Treating model confidence as proof of correctness.
- Measuring usefulness solely through model-graded output.

---

## 4. Design principles

### 4.1 AI proposes; deterministic code disposes

Model calls may:

- Verify whether an operation is supported by evidence.
- Classify sensitivity, scope, durability, and applicability.
- Rerank already-authorized recall candidates.
- Critique recommendation proposals.
- Judge qualitative differences in controlled evaluations.
- Summarize health evidence and suggest remedies.

Model calls must not directly:

- Write files.
- Expand a privacy scope.
- Select an unapproved backend.
- Bypass schema checks.
- Convert a candidate fact into an active fact without deterministic criteria.
- Install a recommendation.
- Declare tests or builds successful.
- Override a user's explicit rejection or pin.

### 4.2 Destructive operations require stronger evidence

The evidence threshold for an operation should scale with its impact:

- Add candidate fact: moderate support.
- Promote candidate to active: strong support or explicit approval.
- Reword active fact: strong support.
- Supersede active fact: stronger support.
- Tombstone active fact: strongest support plus recoverability.
- Change project or privacy scope: deterministic policy only.

### 4.3 Local truth, disposable acceleration

Markdown remains authoritative. SQLite, FTS, embeddings, cached digests, evaluation summaries, and reranking caches are derived state. Every derived artifact must be rebuildable from the store.

### 4.4 Fail-soft does not mean silent

Agent hooks should continue to exit zero, but every attempted operation should leave a bounded local receipt that can be inspected and aggregated.

### 4.5 Restrictive defaults

When classification is missing, malformed, or low-confidence:

- Sensitivity defaults upward.
- Scope defaults inward.
- Egress defaults to disallowed.
- Automatic recall defaults to excluding the uncertain fact.
- Recommendation targets default to project-local.

---

## 5. Current implementation anchors

The plan assumes the current architecture and key implementation points:

- `src/neurobase/curator/engine.py`
  - `PLAN_SYSTEM`
  - `_apply_upserts()`
  - `_synthesize()`
  - `curate()`
- `src/neurobase/adapters/recall_common.py`
  - `_node_bodies()`
  - `_assemble()`
  - `build_context()`
- `src/neurobase/core/redact.py`
  - `redact()`
  - `redact_command()`
- `src/neurobase/core/search.py`
  - `_score()`
  - `search()`
- `src/neurobase/core/projects.py`
  - `load_registry()`
  - `register_project()`
  - `resolve_project()`
- `src/neurobase/recommender/corpus.py`
  - `Corpus`
  - `EvidenceRef`
  - `jaccard_similarity()`
  - `is_near_duplicate()`
- `src/neurobase/recommender/miner.py`
  - `_system_prompt()`
  - `mine()`
- `src/neurobase/recommender/ranker.py`
  - `rank()`
  - `_rank_one()`
- `src/neurobase/recommender/metrics.py`
  - `compute_metrics()`
  - `_survival_one()`
  - `_recurrence_reduction()`
- `src/neurobase/core/config.py`
  - `CurateConfig`
  - `InjectConfig`
  - `RecommendConfig`
- `docs/known-gaps.md`
  - `G1` schema-guard defect

---

## 6. Priority and dependency map

| Priority | Workstream | Why it comes first | Depends on |
|---|---|---|---|
| P0 | Centralized store validation | Prevents incompatible-store reads and writes | None |
| P0 | Egress policy and project profiles | Establishes the trust boundary for every later AI call | Store validation |
| P0 | Hook receipts and degraded-state reporting | Makes failures visible before adding more background behavior | Store validation |
| P1 | Candidate facts and curator verification | Prevents incorrect durable memory | Egress policy, AI-call budget |
| P1 | Scope and sensitivity metadata | Required for safe recall and cross-project mining | Egress policy |
| P2 | Relevance-based recall | Improves daily product value after fact quality is controlled | Fact metadata, search index |
| P2 | Hybrid retrieval | Supports recall, MCP, duplicate detection, and evaluation | Derived index infrastructure |
| P3 | Recommender critic and durability gates | Prevents bad skill promotion | Scope metadata, retrieval |
| P3 | Better recommender metrics | Measures whether recommendations help | Evaluation event model |
| P4 | Adapter SDK and monorepo subprojects | Expands coverage after core correctness stabilizes | Store/profile model |
| P4 | Optional multi-machine strategy | Requires conflict and privacy rules | Profiles, stable schemas |

---

# 7. Issue 1 — Curator plans can preserve incorrect facts

## 7.1 Problem

The curator currently validates whether a model response can be parsed and whether individual fields are structurally usable. It does not independently establish that a valid upsert, supersession, or tombstone is supported by the cited raw material. A model can therefore emit valid JSON that contains an incorrect interpretation.

This creates a feedback risk:

1. A session is misunderstood.
2. The misunderstanding is written as an active curated fact.
3. The synthesized node includes it.
4. Future agents receive it as background context.
5. Later sessions may treat the injected claim as evidence, reinforcing the error.

## 7.2 Solution A — Independent AI verification pass

### 7.2.1 Architecture

Insert a verification stage between `brain.plan_json()` and `_apply_upserts()`:

```text
raw + curated
    -> curator planner model
    -> proposed operation set
    -> independent verifier model
    -> deterministic policy gate
    -> apply accepted operations
```

The verifier should receive only the evidence needed for each operation, not the entire store when avoidable.

### 7.2.2 Verification contract

Each planned operation receives a stable `operation_id` generated by Neurobase before the verifier call.

Verifier output:

```json
{
  "verdicts": [
    {
      "operation_id": "op-0001",
      "verdict": "supported",
      "confidence": 0.93,
      "supporting_raws": ["20260720_codex_ab12cd34.md"],
      "contradicting_raws": [],
      "claim_type": "decision",
      "reason": "The user explicitly selected PostgreSQL over SQLite for production storage."
    }
  ]
}
```

Allowed verdicts:

- `supported`
- `unsupported`
- `ambiguous`
- `contradicted`
- `insufficient-evidence`

### 7.2.3 Deterministic enforcement

Suggested defaults:

| Operation | Minimum verifier confidence | Additional requirement |
|---|---:|---|
| Candidate upsert | 0.75 | At least one resolvable evidence reference |
| Active-fact reword | 0.90 | Existing and proposed claims must be compared |
| Supersession | 0.95 | New fact must explicitly conflict with or replace old fact |
| Tombstone | 0.97 | No active supporting evidence within retention window |

The verifier cannot broaden scope, change slugs, or add operations. It can only evaluate operations proposed by the planner.

### 7.2.4 Backend independence

Add optional configuration:

```toml
[curate.verify]
enabled = true
backend = "auto-separate"
model = ""
require_distinct_backend = false
max_operations_per_call = 25
```

`auto-separate` should prefer a different backend or model family from the planner when available. The purpose is not to assume one vendor is more accurate. It is to reduce correlated failure from asking one model to approve its own output.

### 7.2.5 Failure behavior

- Verifier timeout: keep affected raws unconsumed.
- Malformed verifier output: keep affected raws unconsumed.
- Mixed verdicts: apply only operations that pass policy; retain raws supporting rejected operations unless every material claim from that raw was handled.
- Budget exhaustion: stop cleanly and leave remaining raws unconsumed.

### 7.2.6 Required code changes

- New `src/neurobase/curator/verify.py`
- Add `Operation`, `VerificationVerdict`, and `VerifiedPlan` dataclasses.
- Update `curate()` to normalize operations before verification.
- Add verifier-call accounting to `PassBudget`.
- Extend curator log with proposed, verified, rejected, ambiguous, and contradicted counts.

### 7.2.7 Tests

- Planner emits a supported upsert; verifier approves; fact is written.
- Planner emits a hallucinated upsert; verifier rejects; raw remains unconsumed.
- Verifier attempts to add an operation; extra operation is ignored.
- Verifier cites a raw not present in the operation evidence; verdict is invalid.
- Tombstone below threshold is not applied.
- Distinct-backend selection obeys egress policy.
- Budget exhaustion leaves the store consistent.

### 7.2.8 Acceptance criteria

- No curator-planned destructive operation is applied without passing deterministic verification policy.
- Every applied operation has resolvable supporting provenance.
- Every rejected operation is visible in logs and dry-run output.
- Hook-triggered curation remains bounded.

## 7.3 Solution B — Candidate fact lifecycle

### 7.3.1 New states

Replace binary active/tombstoned handling with:

```text
candidate -> active -> disputed -> tombstoned
```

Suggested frontmatter:

```yaml
status: candidate
confidence: 0.82
scope: project
sensitivity: internal
first_observed_at: 2026-07-20T18:00:00Z
last_confirmed_at: null
confirming_sessions:
  - codex:session-123
provenance:
  - raw/20260720_codex_ab12cd34.md
```

### 7.3.2 Promotion rules

A fact becomes active when any of the following deterministic conditions are met:

- The user explicitly approves it.
- It was explicitly saved through `memory_remember`.
- It is independently observed in two or more sessions.
- It is confirmed by a successful command, test, build, or committed configuration change.
- It passes the independent verifier above and a stricter configured confidence threshold.

Candidate facts remain searchable but are excluded from automatic startup injection by default.

### 7.3.3 Dispute rules

A fact becomes `disputed` when:

- New evidence contradicts it but does not meet supersession threshold.
- A user marks it questionable.
- A verifier reports contradiction.
- An accepted rule produces a failed replay or repeated correction.

Disputed facts are excluded from automatic recall and shown in `neurobase facts review`.

### 7.3.4 CLI additions

```text
neurobase facts list --status candidate
neurobase facts show <slug>
neurobase facts approve <slug>
neurobase facts dispute <slug> --reason "..."
neurobase facts reject <slug>
neurobase facts history <slug>
```

### 7.3.5 Recommendation

Implement both solutions. Verification reduces bad candidates; lifecycle states prevent one verified-but-still-wrong interpretation from becoming immediately authoritative.

---

# 8. Issue 2 — Automatic recall is not relevance-based

## 8.1 Problem

Startup recall currently reads synthesized nodes alphabetically and fills the character budget in that order. This can omit the most relevant context while including unrelated material. It also creates brittle behavior as filenames change.

## 8.2 Solution A — AI reranking over authorized candidates

### 8.2.1 Two-stage retrieval

The model should never search the whole store directly. First, deterministic retrieval selects a bounded candidate set. Then an AI reranker orders those candidates.

Candidate generation inputs:

- Current project and profile.
- Current branch.
- `git diff --name-only`.
- Recently modified files.
- Current working subpath.
- Last successful session topic.
- Open project status.
- Optional first user prompt for agents that support post-prompt recall.

### 8.2.2 Reranker input

```json
{
  "task_context": {
    "branch": "feature/mcp-search",
    "changed_files": ["src/neurobase/core/search.py"],
    "cwd": "src/neurobase/core"
  },
  "candidates": [
    {
      "id": "fact:search-ranking",
      "title": "Current search ranking behavior",
      "snippet": "Search uses whole-word term frequency...",
      "metadata": {
        "paths": ["src/neurobase/core/search.py"],
        "updated_at": "...",
        "scope": "project"
      }
    }
  ]
}
```

Output:

```json
{
  "ranking": [
    {"id": "fact:search-ranking", "score": 0.96, "reason": "Direct path match"}
  ]
}
```

### 8.2.3 Deterministic controls

- Only IDs present in the candidate set are accepted.
- Privacy and scope filters run before the model call.
- The model cannot retrieve excluded facts.
- Packing is deterministic after ranking.
- Low-confidence or failed reranking falls back to deterministic ranking.
- Reranker payload contains snippets, not full raw transcripts.

### 8.2.4 Caching

Cache reranking results by:

```text
project + branch + changed-file fingerprint + candidate-index version
```

Short-lived caching prevents repeated startup calls when task context has not changed.

### 8.2.5 Tests

- Direct path match outranks unrelated alphabetically earlier nodes.
- Model returns unknown ID; it is ignored.
- Sensitive candidate never reaches reranker.
- Reranker failure falls back deterministically.
- Packing never exceeds `max_chars`.

## 8.3 Solution B — Deterministic weighted retrieval and budget packing

### 8.3.1 Fact metadata

Add optional metadata during curation:

```yaml
topics: [mcp, search, ranking]
related_paths:
  - src/neurobase/core/search.py
symbols:
  - search
  - _score
priority: 0.7
updated_at: 2026-07-20T18:00:00Z
expires_at: null
```

### 8.3.2 Scoring

Suggested score:

```text
score =
  bm25
  + 4.0 * exact_path_overlap
  + 2.5 * parent_path_overlap
  + 2.0 * symbol_overlap
  + 1.5 * branch_term_overlap
  + 1.0 * recency_weight
  + pinned_priority
  - staleness_penalty
  - disputed_penalty
```

Weights should be configuration-driven and benchmarked against a recall test corpus.

### 8.3.3 Packing

Do not stop when one node does not fit. Use one of:

- Greedy score-per-character packing.
- Bounded 0/1 knapsack over the top candidate set.
- Section-level packing when nodes have independently addressable sections.

The selector should maximize total value under the character budget while guaranteeing a small reserved budget for:

- Current decisions.
- Current blockers.
- Safety constraints.

### 8.3.4 Recommendation

Ship deterministic weighted retrieval first. Add AI reranking as an optional quality layer once candidate metadata and privacy filtering are stable.

---

# 9. Issue 3 — Sensitive-data egress is incompletely controlled

## 9.1 Problem

Regex redaction is necessary but cannot identify all sensitive content. Plain-language passwords, client names, account identifiers, private deal information, and confidential source fragments may not match fixed patterns. Neurobase can also distill a transcript through a different configured backend from the agent that produced it.

## 9.2 Solution A — Local semantic DLP model

### 9.2.1 Processing pipeline

```text
raw transcript
  -> deterministic regex redaction
  -> local semantic DLP classification
  -> deterministic span replacement
  -> policy validation
  -> remote or CLI brain call
```

The semantic DLP model must be local when it is used to decide whether content is safe to transmit. An external classifier would defeat the purpose.

### 9.2.2 Detection categories

- Credentials expressed in prose.
- Personally identifying information.
- Client or tenant names.
- Account and loan identifiers.
- Internal hosts and URLs.
- Confidential transaction details.
- Proprietary source or prompts.
- Legal, medical, or regulated data.

### 9.2.3 Structured output

```json
{
  "spans": [
    {
      "start": 421,
      "end": 447,
      "label": "client-name",
      "confidence": 0.94
    }
  ],
  "document_risk": "confidential"
}
```

Deterministic code validates non-overlapping offsets against the exact input string and performs replacement. The model never returns modified text.

### 9.2.4 Failure policy

For `local-only` or `restricted` projects:

- Local DLP unavailable: do not make remote call.
- Malformed output: do not make remote call.
- Low-confidence risk: default to redaction or local-only backend.
- Payload too large: chunk locally with overlap, then merge spans deterministically.

### 9.2.5 Configuration

```toml
[privacy.dlp]
enabled = true
backend = "ollama"
model = "local-dlp-model"
min_span_confidence = 0.70
block_remote_on_failure = true
```

## 9.3 Solution B — Per-project and per-profile egress policy

### 9.3.1 Registry redesign

Replace bare root lists with project records:

```toml
[projects.neurobase]
roots = ["/Users/me/code/neurobase"]
profile = "open-source"
privacy = "approved-cli"
allowed_brains = ["claude-cli", "codex-cli"]
allow_transcript_distill = true
allow_cross_agent_backend = false
allow_cross_project_mining = true

[profiles.open-source]
default_scope = "profile"
allow_user_skill_proposals = true
```

Suggested privacy modes:

- `local-only`
- `same-agent-cli`
- `approved-cli`
- `api-allowed`

### 9.3.2 Central egress gate

Create one API used by every brain call:

```python
EgressDecision authorize_egress(
    store: StoreHandle,
    project: ProjectPolicy,
    purpose: EgressPurpose,
    backend: BrainDescriptor,
    payload_metadata: PayloadMetadata,
)
```

Purposes include:

- `curate-plan`
- `curate-verify`
- `transcript-distill`
- `recall-rerank`
- `recommend-mine`
- `recommend-critic`
- `evaluation-judge`
- `health-diagnose`

The gate returns allow, deny, or require-local-DLP. No caller may invoke a backend without an allow decision.

### 9.3.3 Inspection command

```text
neurobase egress inspect --purpose curate-plan --project neurobase
```

Output should include:

- Selected backend.
- Project privacy mode.
- Whether semantic DLP ran.
- Bytes before and after redaction.
- Redaction counts by category.
- Exact final payload with a `--show-payload` flag.
- Reasons for allow or deny.

### 9.3.4 Recommendation

Egress policy is mandatory P0 work. Semantic DLP is an optional but strongly recommended layer for sensitive profiles.

---

# 10. Issue 4 — Fail-soft hooks can fail invisibly

## 10.1 Problem

Hooks intentionally catch errors and exit successfully. This protects session startup and teardown, but users can incorrectly assume capture, curation, or recall is working.

## 10.2 Solution A — Deterministic hook receipts with AI-assisted diagnosis

### 10.2.1 Receipt format

Every hook attempt writes a bounded event:

```json
{
  "event_id": "01J...",
  "event": "session-end",
  "agent": "codex",
  "agent_version": "...",
  "project": "neurobase",
  "started_at": "...",
  "finished_at": "...",
  "duration_ms": 81,
  "status": "capture-failed",
  "error_type": "rollout-not-found",
  "error_fingerprint": "sha256:...",
  "bytes_written": 0
}
```

Do not store raw exception text when it may contain sensitive paths or content. Normalize errors and hash the detailed diagnostic where needed.

### 10.2.2 Storage

Use a bounded local ring:

```text
<root>/health/hook-events.jsonl
```

Retention options:

- Maximum events.
- Maximum bytes.
- Maximum age.

Rotate atomically.

### 10.2.3 Deterministic health checks

- More than N consecutive capture failures.
- No successful capture despite recent agent hook events.
- No successful recall in N session starts.
- Unknown agent version.
- Growing unconsumed backlog.
- Repeated curator `partial` or `error` status.
- Last successful event older than configured threshold.
- Hook shim path no longer exists.

### 10.2.4 AI diagnostic call

`neurobase doctor --explain` may send a sanitized aggregate to a configured model:

```json
{
  "checks": [...],
  "recent_error_types": [...],
  "agent_versions": [...],
  "config_summary": {...}
}
```

The model returns an ordered remediation plan. Deterministic checks remain the source of truth; the model explains them.

## 10.3 Solution B — Persistent degraded state and notifications

### 10.3.1 Health state

Maintain:

```text
<root>/health/state.json
```

Example:

```json
{
  "overall": "degraded",
  "subsystems": {
    "capture:codex": {
      "state": "failed",
      "consecutive_failures": 4,
      "last_success_at": "...",
      "last_failure_at": "..."
    }
  }
}
```

### 10.3.2 User surfaces

- `neurobase status` displays freshness and degraded states.
- `neurobase doctor` exits nonzero when health is degraded.
- Optional OS notification after threshold crossing.
- Next successful startup injection may include a short operational warning.
- Recovery clears the flag after a configured number of successes.

### 10.3.3 Acceptance criteria

- Hooks still always exit zero.
- Every hook attempt produces either a receipt or a detectable receipt-write failure counter.
- A broken adapter becomes visible through `status` without requiring verbose logs.
- Health files remain bounded.

---

# 11. Issue 5 — Repetition can promote mistakes or temporary workarounds

## 11.1 Problem

The recommender currently uses recurrence, session breadth, agent breadth, project breadth, and recency. Those are useful evidence-of-frequency signals but not evidence-of-correctness or durability.

## 11.2 Solution A — Adversarial AI critic

### 11.2.1 Placement

```text
corpus -> miner -> deterministic ranker -> critic -> proposal store
```

The critic receives:

- Candidate draft.
- Full resolved evidence.
- Counterevidence retrieved from the same scope.
- Test/build outcomes when available.
- Candidate scope and proposed target.
- Rejected-proposal history.

### 11.2.2 Classification

- `durable-best-practice`
- `project-specific-requirement`
- `temporary-workaround`
- `repeated-failure`
- `personal-preference`
- `unsafe`
- `unsupported`
- `insufficient-evidence`

Output:

```json
{
  "candidate_id": "prefer-store-handle",
  "classification": "durable-best-practice",
  "confidence": 0.91,
  "recommended_scope": "project",
  "risk_level": "low",
  "counterevidence": [],
  "conditions_where_wrong": [
    "Read-only doctor and purge paths require explicit exceptions."
  ],
  "expiration_trigger": null
}
```

### 11.2.3 Deterministic rules

- `temporary-workaround` becomes an expiring project note, not a user skill.
- `repeated-failure`, `unsafe`, or `unsupported` is dropped and logged.
- `project-specific-requirement` cannot target user scope.
- `insufficient-evidence` remains unpersisted or enters a low-priority watch list.
- The critic cannot increase scope beyond the miner or project policy.

## 11.3 Solution B — Deterministic durability and success gates

Add configurable gates:

```toml
[recommend]
min_occurrences = 3
min_breadth_sessions = 2
min_days_span = 7
require_success_signal = true
trial_days = 14
```

Potential success signals:

- Tests passed after applying the workflow.
- Build passed.
- User explicitly approved the result.
- A commit containing the approach survived without reversion.
- The same pattern appeared in distinct tasks.
- No later correction contradicted it.

Temporary-language detection should be deterministic first:

- `for now`
- `temporary`
- `workaround`
- `until upstream fixes`
- `hack`
- `incident`
- `migration only`

These markers do not automatically reject a candidate, but they require critic review and force project-local, expiring scope unless explicitly overridden.

### 11.3.1 Trial lifecycle

```text
proposed -> accepted-trial -> accepted-permanent
```

Trial artifact metadata:

```yaml
neurobase_status: accepted-trial
neurobase_proposal: prefer-store-handle
expires_at: 2026-08-03T00:00:00Z
```

Permanent promotion requires:

- Trial window elapsed.
- Artifact remained enabled.
- No negative evaluation threshold crossed.
- User confirmation or configured auto-promote policy.

### 11.3.2 Recommendation

Use both critic classification and deterministic durability gates. Frequency alone should never be enough for permanent user-level promotion.

---

# 12. Issue 6 — Cross-project mining can cross boundaries

## 12.1 Problem

The corpus loader combines facts and captures from every registered project. Without explicit privacy and applicability boundaries, confidential content can share a prompt with unrelated projects, and project-local constraints can be generalized into global skills.

## 12.2 Solution A — AI metadata classification with restrictive deterministic enforcement

### 12.2.1 Classification fields

At curation time, an additional classifier may propose:

```yaml
domain: infrastructure
sensitivity: internal
scope: project
client_id: null
technologies: [python, typer]
applicability:
  operating_systems: [macos, linux, windows]
  agents: [claude, codex]
confidence:
  sensitivity: 0.89
  scope: 0.83
```

### 12.2.2 Enforcement rules

- `restricted` and `confidential` facts cannot participate in cross-project mining.
- Different non-null `client_id` values never share a miner payload.
- `scope: project` cannot generate user-level artifacts.
- `scope: profile` can only cross projects within one profile.
- Classification confidence below threshold defaults to project scope and higher sensitivity.
- Users may narrow scope manually; widening requires explicit approval.

### 12.2.3 Classifier behavior

The classifier returns labels only. It does not edit fact text or policy records. Deterministic code validates labels against enums and applies restrictive defaults.

## 12.3 Solution B — Isolated profiles and explicit cross-project commands

### 12.3.1 Profile model

Suggested profiles:

- `personal`
- `employer`
- `open-source`
- `client-a`
- `client-b`

Each profile receives:

- Separate project namespace.
- Separate proposal store.
- Separate recommender ledger.
- Separate egress policy.
- Separate allowed backends.
- Separate default artifact targets.

The physical store may remain under one root, but APIs must require a profile-qualified handle.

### 12.3.2 Command behavior

Default:

```text
neurobase recommend run --project neurobase
```

Explicit profile mining:

```text
neurobase recommend run --profile open-source --cross-project
```

User-level proposal eligibility:

- Evidence from at least two projects.
- All projects belong to one profile.
- All evidence permits profile or user scope.
- No confidential or client-bound facts.

### 12.3.3 Recommendation

Profiles are a required privacy primitive. AI classification adds useful judgment but must operate inside profile and policy boundaries.

---

# 13. Issue 7 — Metrics do not measure real improvement

## 13.1 Problem

Current metrics primarily measure whether users accept proposals, whether emitted files remain unchanged, and whether lexical near-duplicates occur less often. They do not establish that code quality, correctness, speed, or user effort improved.

## 13.2 Solution A — Paired task replay with independent AI judge

### 13.2.1 Evaluation design

For a recommendation with enough evidence, construct a representative task fixture and run two isolated trials:

```text
control: agent without accepted artifact
treatment: same agent with accepted artifact
```

Both runs should use:

- Same repository commit.
- Same task prompt.
- Same model and reasoning settings.
- Same tool permissions.
- Separate clean worktrees.
- Fixed timeout and call budget.

### 13.2.2 Objective measurements

- Test pass/fail.
- Build pass/fail.
- Lint/type-check results.
- Number of changed files.
- Number of retries.
- Tool-call count.
- Token use when available.
- Time to first green test.
- Final diff size.
- Revert or cleanup operations.

### 13.2.3 Qualitative judge

An independent judge model receives anonymized, randomly ordered outputs and a rubric:

- Instruction compliance.
- Maintainability.
- Completeness.
- Safety.
- Consistency with repository conventions.

The judge must not know which run is treatment. Objective failures override qualitative preference.

### 13.2.4 Result model

```json
{
  "proposal": "prefer-store-handle",
  "control": {...},
  "treatment": {...},
  "objective_winner": "treatment",
  "judge_winner": "treatment",
  "confidence": 0.88,
  "regressions": []
}
```

### 13.2.5 Limitations

Replay is expensive and not valid for every recommendation. It should be used for high-impact skills, rules that affect many sessions, or promotion from trial to permanent status.

## 13.3 Solution B — Observed workflow outcome metrics

Rename current metrics for accuracy:

- `precision` -> `acceptance_rate`
- `survival` -> `artifact_unchanged_rate`
- `recurrence_reduction` -> `lexical_recurrence_ratio`

Add:

- Relevant-session success rate.
- Test-failure rate.
- Build-failure rate.
- Corrective prompts per relevant session.
- Reverted-change rate.
- Artifact edit rate.
- Time to first successful validation.
- Rule-load rate in eligible sessions.
- Post-acceptance contradiction count.

### 13.3.1 Matching

Do not compare all sessions before and after. Match sessions by:

- Project.
- Task category.
- Technologies.
- Relevant paths.
- Agent.
- Similar complexity band.

Use fixed sample windows, such as 20 eligible sessions before and 20 after.

### 13.3.2 Acceptance criteria

- Metrics names accurately describe what they measure.
- Objective outcomes are stored separately from model judgments.
- A recommendation can be marked regressed and automatically returned to trial or disputed state.

---

# 14. Issue 8 — Search and duplicate detection are mostly lexical

## 14.1 Problem

Exact-term frequency and token-set Jaccard similarity miss semantic paraphrases, acronyms, renamed concepts, and differently worded descriptions of the same problem.

## 14.2 Solution A — Hybrid retrieval with embeddings and AI reranking

### 14.2.1 Derived index

Create a rebuildable SQLite index:

```text
<root>/.index/neurobase.sqlite3
```

Tables:

- `documents`
- `fts_documents` using FTS5
- `embeddings`
- `metadata`
- `index_state`

Document fields:

- Stable document ID.
- Profile.
- Project.
- Kind.
- Slug.
- Body.
- Paths.
- Symbols.
- Topics.
- Scope.
- Sensitivity.
- Updated timestamp.
- Content hash.

### 14.2.2 Retrieval pipeline

1. Apply profile, project, scope, and sensitivity filters.
2. Retrieve lexical top K.
3. Retrieve vector top K.
4. Union and deduplicate.
5. Apply recency and path boosts.
6. Optional AI rerank.
7. Return top N.

Use reciprocal-rank fusion or a configured weighted combination rather than directly comparing BM25 and vector scores.

### 14.2.3 Embedding backend policy

Embeddings are also egress. They must pass through the same egress gate. Sensitive profiles should use a local embedding model or disable vectors.

### 14.2.4 Rebuild behavior

```text
neurobase index rebuild
neurobase index status
neurobase index verify
```

The index should detect changed documents by content hash and update incrementally.

## 14.3 Solution B — Stronger deterministic lexical retrieval

Before or without vectors:

- Use SQLite FTS5 with BM25.
- Add stemming.
- Add phrase search.
- Add prefix queries.
- Index paths and symbols separately.
- Add acronym and synonym maps.
- Add weighted fields.
- Add character n-grams for identifiers.

For duplicate detection:

- Weighted word shingles.
- MinHash signatures.
- SimHash for near-identical documents.
- Separate code-token and prose-token similarity.

### 14.3.1 Recommendation

Implement FTS5 first because it is local, explainable, and low-risk. Add embeddings behind a feature flag and the egress policy.

---

# 15. Issue 9 — Store-schema validation is inconsistent

## 15.1 Problem

Schema validation occurs at individual call sites. This is easy to forget and has already allowed pre-guard registry reads and writes. This problem is deterministic and should not use AI.

## 15.2 Solution A — Mandatory validated `StoreHandle`

### 15.2.1 API

```python
store = StoreHandle.open(
    root,
    mode=StoreMode.READ,
    compatibility=CompatibilityPolicy.REQUIRE_SUPPORTED,
)
```

Modes:

- `READ`
- `WRITE`
- `DOCTOR`
- `MIGRATE`
- `PURGE`

### 15.2.2 Enforcement

- Constructor is private.
- All storage and project APIs require `StoreHandle`.
- Raw `Path` overloads are removed or deprecated.
- `DOCTOR` can inspect unsupported stores without mutating them.
- `PURGE` can delete an unsupported store only after explicit user confirmation.
- MCP startup remains alive but tools return structured incompatibility errors.

### 15.2.3 Validation

`open()` checks:

- `store.toml` schema.
- Registry parseability.
- Required directory permissions.
- Migration lock.
- Incomplete transaction markers.
- Store-root identity.

### 15.2.4 Migration plan

1. Introduce handle alongside current API.
2. Convert core store and project functions.
3. Convert curator, adapters, MCP, recommender, and CLI.
4. Remove raw-path APIs.
5. Add CI test preventing imports of deprecated helpers.

## 15.3 Solution B — Guard every filesystem boundary

A smaller but less durable option:

- Call schema validation inside `load_registry()`.
- Call it inside `memory_dir()`.
- Call it inside every proposal and ledger accessor.
- Call it inside every write helper.
- Construct MCP server with validated compatibility state.

Add an AST-based CI check that forbids direct filesystem access beneath the store root outside approved modules.

### 15.3.1 Integration test matrix

Run every CLI command and MCP tool against:

- Supported schema.
- Newer unsupported schema.
- Missing schema.
- Malformed schema.
- Read-only store.
- Partially migrated store.

Verify exact mutation behavior by hashing the entire store before and after.

### 15.3.2 Recommendation

Use the `StoreHandle`. Per-call-site guards recreate the same omission risk.

---

# 16. Issue 10 — Agent, monorepo, platform, and sync scope is narrow

## 16.1 Problem

V1 intentionally supports Claude Code and Codex CLI, collapses a Git repository to one project identity, does not model teams, and has no first-class multi-machine story.

## 16.2 Solution A — Adapter SDK with AI-assisted scaffolding

### 16.2.1 Interfaces

```python
class CaptureAdapter(Protocol):
    def parse_event(self, event: AgentEvent) -> SessionCapture | None: ...

class RecallAdapter(Protocol):
    def render_context(self, context: RecallContext) -> AgentEnvelope: ...

class InstallerAdapter(Protocol):
    def inspect(self) -> InstallationState: ...
    def diff(self, desired: DesiredInstallation) -> ConfigPatch: ...

class CapabilityManifest(TypedDict):
    capture: bool
    startup_recall: bool
    mid_session_mcp: bool
    config_install: bool
```

Load adapters through Python entry points:

```toml
[project.entry-points."neurobase.adapters"]
gemini = "neurobase_gemini:adapter"
```

### 16.2.2 Conformance suite

Every adapter must pass:

- Fail-soft event parsing.
- Redaction-before-write.
- Empty-session behavior.
- Stable session identity.
- Startup context cap.
- Installer idempotence.
- Backup and uninstall ownership.
- Unknown-version behavior.

### 16.2.3 AI scaffolding

`neurobase adapter probe` records redacted sample payloads. An optional model call may generate:

- Initial parser code.
- Field mapping.
- Fixture candidates.
- Edge-case hypotheses.
- Installer draft.

Generated code is never installed automatically and must pass the conformance suite.

## 16.3 Solution B — Generic MCP mode and subproject-aware registration

### 16.3.1 Capability tiers

1. Full hooks: capture plus automatic recall.
2. MCP mode: search, read, and explicit remember.
3. Managed instruction mode: generated local `AGENTS.md` or equivalent.

This allows partial support for any MCP-capable agent before a full adapter exists.

### 16.3.2 Monorepo projects

Registry:

```toml
[projects.web]
roots = ["/repo"]
match_subpath = "apps/web"
profile = "employer"

[projects.api]
roots = ["/repo"]
match_subpath = "apps/api"
profile = "employer"
```

Resolution should preserve both:

- Git common root for worktree identity.
- Actual working path for longest subproject match.

### 16.3.3 Multi-machine strategy

Initial supported strategy should be documentation plus conflict-safe primitives, not a hosted service:

- Git-sync only curated facts, nodes, proposals, and ledger.
- Keep raw captures local by default.
- Use stable ULIDs for events.
- Treat ledger as append-only.
- Detect fact conflicts by content hash and lineage.
- Never silently merge conflicting active facts.
- Mark conflicted facts disputed until reviewed.

### 16.3.4 Team support boundary

Do not infer team scope from a shared repository. Team memory requires explicit identity, access control, and contribution provenance. Until those exist, profiles remain single-user boundaries.

---

# 17. Cross-cutting AI call architecture

## 17.1 Proposed calls

| Call | Purpose | Input scope | Output | Required fallback |
|---|---|---|---|---|
| Semantic DLP | Detect sensitive spans missed by regex | Local document chunks | Spans and risk labels | Block remote egress or use local backend |
| Curator verifier | Check support for planned mutations | Proposed operations plus evidence | Verdict per operation | Leave raws unconsumed |
| Scope classifier | Suggest sensitivity and applicability | Curated fact plus provenance | Enum labels and confidence | Restrictive defaults |
| Recall reranker | Order authorized recall candidates | Metadata and bounded snippets | Ranked IDs | Deterministic ranking |
| Recommender critic | Distinguish durable practice from mistake/workaround | Candidate plus evidence/counterevidence | Classification and risk | Do not persist proposal |
| Evaluation judge | Compare qualitative outcomes | Anonymized control/treatment results | Rubric scores | Objective metrics only |
| Health explainer | Translate deterministic findings into remediation | Sanitized health checks | Ordered explanation | Raw deterministic report |
| Adapter scaffolder | Accelerate new adapter development | Redacted fixture payloads | Draft code/tests | Manual implementation |

## 17.2 Shared AI-call requirements

Create one orchestration layer used by all higher-level subsystems:

```python
class JudgmentService:
    def call(
        self,
        purpose: JudgmentPurpose,
        project: ProjectPolicy,
        schema: OutputSchema,
        payload: str,
        budget: JudgmentBudget,
    ) -> JudgmentResult: ...
```

Responsibilities:

- Egress authorization.
- Backend selection.
- Payload-size checks.
- Redaction and optional semantic DLP.
- Structured-output parsing.
- Retry limits.
- Timeouts.
- Call receipts.
- Cost and token accounting when available.
- Model and prompt version recording.

## 17.3 Prompt versioning

Every judgment result should record:

- Purpose.
- Prompt version.
- Model.
- Backend.
- Input content hashes.
- Output hash.
- Timestamp.
- Parser version.

This supports reproducible review without storing duplicate sensitive payloads.

## 17.4 AI calls that should not exist

Do not add model calls for:

- Schema compatibility.
- Path authorization.
- Profile membership.
- Whether a file exists.
- Whether tests passed.
- Whether a hash matches.
- Whether a proposal was accepted.
- Whether a model-returned ID was in the authorized candidate set.
- Whether a hook exceeded a deterministic failure threshold.

---

# 18. Proposed data-model changes

## 18.1 Curated fact frontmatter

```yaml
name: prefer-store-handle
status: candidate
confidence: 0.91
scope: project
sensitivity: internal
profile: open-source
project: neurobase
first_observed_at: 2026-07-20T18:00:00Z
last_confirmed_at: 2026-07-20T18:00:00Z
confirming_sessions:
  - codex:session-123
provenance:
  - raw/20260720_codex_ab12cd34.md
verification:
  prompt_version: curator-verifier-v1
  verdict: supported
  confidence: 0.93
related_paths:
  - src/neurobase/core/store.py
symbols:
  - StoreHandle
topics:
  - schema
  - storage
```

## 18.2 Project policy

```yaml
slug: neurobase
profile: open-source
roots:
  - /Users/me/code/neurobase
match_subpath: null
privacy: approved-cli
allowed_brains:
  - claude-cli
  - codex-cli
allow_transcript_distill: true
allow_cross_agent_backend: false
allow_cross_project_mining: true
```

## 18.3 Recommendation proposal

```yaml
name: prefer-store-handle
status: accepted-trial
classification: durable-best-practice
critic_confidence: 0.91
scope: project
profile: open-source
project: neurobase
trial_started_at: 2026-07-20T18:00:00Z
trial_expires_at: 2026-08-03T18:00:00Z
evidence: [...]
counterevidence: []
evaluation_ids: []
```

## 18.4 Health event

```yaml
event_id: 01J...
subsystem: capture
agent: codex
project: neurobase
status: failed
error_type: rollout-not-found
started_at: ...
finished_at: ...
duration_ms: 81
```

---

# 19. Configuration proposal

```toml
[store]
root = "~/neurobase"

[brain]
backend = "auto"
model = "claude-sonnet-5"
timeout_seconds = 120

[privacy]
default_profile = "personal"
default_privacy = "same-agent-cli"

[privacy.dlp]
enabled = false
backend = "ollama"
model = ""
min_span_confidence = 0.70
block_remote_on_failure = true

[curate]
stale_hours = 12
tombstone_grace_days = 14
distill = "auto"

[curate.verify]
enabled = true
backend = "auto-separate"
model = ""
min_upsert_confidence = 0.75
min_reword_confidence = 0.90
min_supersede_confidence = 0.95
min_tombstone_confidence = 0.97

[facts]
automatic_candidate_promotion = true
min_confirming_sessions = 2
inject_candidates = false
inject_disputed = false

[inject]
max_chars = 6000
retrieval = "fts"
rerank = "off"
max_candidates = 30

[search]
backend = "fts5"
embeddings = "off"
embedding_backend = "local"

[recommend]
min_occurrences = 3
min_breadth_sessions = 2
min_days_span = 7
require_success_signal = true
critic = "on"
trial_days = 14

[health]
receipt_retention_days = 30
receipt_max_bytes = 10485760
consecutive_failure_threshold = 3
notify = false
```

---

# 20. CLI and MCP surface changes

## 20.1 CLI

```text
neurobase facts list
neurobase facts review
neurobase facts approve <slug>
neurobase facts dispute <slug>
neurobase facts history <slug>

neurobase egress inspect
neurobase egress test

neurobase health
neurobase health explain
neurobase health clear <subsystem>

neurobase index status
neurobase index rebuild
neurobase index verify

neurobase recommend evaluate <slug>
neurobase recommend promote <slug>
neurobase recommend rollback <slug>

neurobase profile list
neurobase profile create <name>
neurobase project set-profile <project> <profile>
```

## 20.2 MCP

Add or extend:

- `memory_search`
  - profile and project filters.
  - lexical, hybrid, or vector mode.
- `memory_read_fact`
  - status, confidence, provenance, and scope.
- `memory_list_candidates`
- `memory_confirm_fact`
  - explicit user-directed confirmation only.
- `memory_health`
- `recommendations_list`
  - critic classification and trial state.

MCP write tools must remain narrow and explicit. Model clients should not be able to widen scope or approve egress policy.

---

# 21. Observability and auditability

## 21.1 Local ledgers

Suggested ledgers:

```text
<root>/health/hook-events.jsonl
<root>/judgments/calls.jsonl
<root>/facts/events.jsonl
<root>/recommender/ledger.jsonl
<root>/evaluations/results.jsonl
```

Each ledger is append-only, bounded where appropriate, and contains hashes rather than duplicated payloads when possible.

## 21.2 Status summaries

`neurobase status` should report:

- Last successful capture by agent.
- Last successful recall by agent.
- Unconsumed raw backlog.
- Candidate, active, disputed, and tombstoned fact counts.
- Curator verification acceptance rate.
- Number of blocked egress attempts.
- Search index freshness.
- Recommendation trial and permanent counts.
- Evaluation wins, regressions, and insufficient-data counts.

---

# 22. Testing strategy

## 22.1 Unit tests

- Structured model-output parsing.
- Confidence and operation-type thresholds.
- Restrictive scope defaults.
- Egress-policy decisions.
- Fact lifecycle transitions.
- Deterministic recall scoring.
- Budget packing.
- Profile isolation.
- Health threshold calculations.
- Trial expiration.
- Metric calculations.

## 22.2 Property-based tests

- No model output can reference an unauthorized ID.
- No scope can widen without explicit approval.
- No unsupported-schema store mutates through normal commands.
- Packing never exceeds configured budget.
- Span redaction preserves non-sensitive text and never emits overlapping replacements.
- Ledger replay produces the same current state.

## 22.3 Integration tests

- Claude capture -> candidate -> verification -> activation -> recall.
- Codex capture -> candidate -> Claude recall.
- Verifier timeout leaves raw unconsumed.
- Sensitive project blocks API backend.
- Hook failure produces receipt and degraded status.
- Cross-profile recommender request is rejected.
- Accepted-trial recommendation expires or promotes correctly.
- FTS index rebuild reproduces search results.
- MCP returns structured unsupported-schema errors without being dropped.

## 22.4 Adversarial test corpus

Include sessions containing:

- Contradictory decisions.
- Sarcasm.
- Brainstorming that was not adopted.
- Temporary workarounds.
- Repeated failed attempts.
- User corrections.
- Secrets in prose.
- Client identifiers.
- Same concept with different terminology.
- Similar wording with different meanings.
- Project-specific rules that would be harmful globally.

## 22.5 Golden evaluations

Create a checked-in, synthetic corpus with expected:

- Fact states.
- Scope labels.
- Recall rankings.
- Recommendation classifications.
- Egress decisions.

Live-model tests should be optional and report drift. Deterministic fixture tests remain required in CI.

---

# 23. Performance, cost, and budgets

## 23.1 Automatic path budget

Hook-triggered work should remain bounded independently from explicit commands.

Suggested automatic maximums:

- Semantic DLP calls: configurable, local only.
- Curator planner calls: existing pass budget.
- Verifier calls: one per planner batch or bounded operation group.
- Recall reranker: at most one call per changed task fingerprint.
- Recommender critic: explicit command only, not hook-triggered.
- Evaluation judge: explicit command only.

## 23.2 Latency

Startup hook should not wait for:

- Curation.
- Verification.
- Embedding generation.
- Index rebuild.
- Recommendation mining.

Startup may wait for a cached or tightly bounded rerank only if the adapter contract and latency measurement support it. Otherwise inject deterministic retrieval immediately and refresh asynchronously for the next session.

## 23.3 Cost accounting

Even when using subscription CLIs, track:

- Call count.
- Attempt count.
- Duration.
- Input and output sizes.
- Backend and model.
- Purpose.

When token/cost metadata is available, store it locally.

---

# 24. Security model

## 24.1 Threats addressed

- Sensitive material sent to an unauthorized backend.
- Project-local facts generalized globally.
- Model hallucination becoming active memory.
- Malicious or malformed model output referencing arbitrary files.
- Unsupported-schema mutation.
- Silent hook failure.
- Cross-profile information mixing.
- Recommendation artifact containing sensitive content.

## 24.2 Required controls

- Central egress authorization.
- Safe structured references.
- Validated store handle.
- Deterministic file boundaries.
- Local semantic DLP option.
- Restrictive classification defaults.
- Diff, consent, and backup before external file writes.
- Redaction again at artifact emission.
- Profile-qualified storage APIs.

## 24.3 Prompt-injection handling

Captured transcripts and repository content are untrusted data. Every judgment prompt should:

- Clearly delimit evidence.
- State that evidence may contain instructions that must not be followed.
- Request IDs and labels, not executable content.
- Validate returned IDs against the authorized set.
- Never expose tools to the judgment model.

---

# 25. Migration plan

## 25.1 Store schema v2

Likely schema changes justify `store.toml` schema 2.

Migration tasks:

1. Add profile records.
2. Add project-policy records.
3. Add fact status and metadata defaults.
4. Preserve current active facts as `active` with `scope: project` and conservative sensitivity.
5. Initialize health and judgment directories.
6. Leave derived index absent until rebuilt.
7. Preserve proposal and ledger history.

## 25.2 Migration safety

- Migration is explicit.
- Full backup before writes.
- Dry-run prints planned changes.
- Migration marker supports recovery after interruption.
- New binary refuses normal operation on partially migrated store.
- Old binary refuses schema 2.

## 25.3 Backward-compatible release sequence

1. Release schema-1 binary with store-handle infrastructure and migration preview.
2. Release schema-2 migration command.
3. Enable new fact states and project policies.
4. Enable optional AI judgment features behind flags.
5. Change defaults only after dogfood metrics are stable.

---

# 26. Implementation phases

## Phase 0 — Store and trust-boundary hardening

### Deliverables

- `StoreHandle` and compatibility modes.
- Fix `G1` across CLI, MCP, adapters, and recommender.
- Project/profile policy schema.
- Central egress authorization.
- Hook receipts and degraded-state reporting.

### Exit criteria

- No normal path can touch an unsupported store.
- Sensitive project policy blocks unauthorized backend calls.
- Broken hooks are visible through `status` and `doctor`.

## Phase 1 — Fact correctness

### Deliverables

- Candidate, active, disputed, and tombstoned states.
- Curator operation normalization.
- Optional independent verifier.
- Fact-review CLI.
- Verification provenance and logs.

### Exit criteria

- One session cannot create automatically injected active memory unless configured criteria are met.
- Destructive operations require high-confidence verification or explicit approval.

## Phase 2 — Retrieval quality

### Deliverables

- Fact metadata.
- FTS5 derived index.
- Deterministic relevance ranking.
- Budget-aware packing.
- Optional AI reranker.

### Exit criteria

- Recall benchmark materially outperforms alphabetical packing.
- Privacy filters run before retrieval results reach a model.

## Phase 3 — Recommender quality

### Deliverables

- Critic call.
- Durability and success gates.
- Trial artifacts.
- Scope-aware proposal eligibility.
- Temporary-workaround handling.

### Exit criteria

- Temporary and project-specific patterns are not emitted as permanent user-level skills.
- Every persisted proposal includes critic and evidence metadata.

## Phase 4 — Outcome evaluation

### Deliverables

- Renamed proxy metrics.
- Relevant-session outcome metrics.
- Paired replay harness.
- Optional blinded AI judge.
- Regression and rollback states.

### Exit criteria

- At least one accepted recommendation can be evaluated using objective control/treatment results.
- A regressed recommendation can be rolled back cleanly.

## Phase 5 — Extensibility

### Deliverables

- Adapter protocol and plugin loading.
- Conformance test suite.
- Generic MCP mode.
- Monorepo subproject matching.
- Multi-machine documentation and conflict model.

### Exit criteria

- A third-party adapter can be developed without modifying core packages.
- Two monorepo subprojects maintain isolated memories.

---

# 27. Review decisions required

1. Should candidate facts be the default for every model-curated upsert, or only low-confidence ones?
2. Must destructive curator operations always use a distinct verifier model?
3. Which privacy modes should ship in the first policy schema?
4. Should semantic DLP be bundled as an optional local-model integration or only documented as an extension point?
5. Should profiles be physical store partitions or logical namespaces under one store?
6. Is startup AI reranking acceptable, or should reranking be limited to mid-session recall until latency data exists?
7. What objective success signals are available consistently across Claude and Codex sessions?
8. Should recommendation trials ever auto-promote, or always require user confirmation?
9. Is SQLite FTS5 acceptable as derived core infrastructure despite the current no-database framing?
10. What parts of multi-machine synchronization belong in product scope versus documentation?

---

# 28. Recommended decisions

- **Candidate facts should be the default** for model-created claims.
- **User-directed facts remain immediately active and pinned.**
- **Supersession and tombstone should require independent verification** when verification is enabled.
- **Profiles should be first-class logical partitions** under one visible store root.
- **Cross-project mining should be explicit**, never the implicit default.
- **FTS5 should ship before embeddings.** SQLite remains derived state, so Markdown stays authoritative.
- **AI reranking should initially be optional and cached.**
- **Recommendation trials should require explicit promotion** until objective evaluation is mature.
- **Schema safety must use a validated store handle**, not additional call-site checks.
- **Semantic DLP should be optional but supported through a stable local-classifier interface.**

---

# 29. Definition of done

This hardening initiative is complete when:

- Unsupported stores cannot be read or mutated by normal operations.
- Every brain call is authorized through one egress policy layer.
- Hook failures are visible without breaking agent sessions.
- Model-curated facts have lifecycle states, provenance, and verification history.
- Automatic recall is relevance-ranked and budget-aware.
- Sensitive and project-local facts cannot leak into unauthorized cross-project prompts.
- Recommendations distinguish durable practices from temporary workarounds and repeated failures.
- Accepted recommendations can be evaluated against objective outcomes.
- Search handles both strong lexical retrieval and optional semantic retrieval.
- A third agent can integrate through a documented adapter contract.
- CI contains deterministic tests for every security and correctness invariant above.

---

# 30. Final recommendation

The next major release should not prioritize broader agent support before memory correctness and trust boundaries are stronger. The highest-value sequence is:

1. Centralize store validation.
2. Add profiles and egress policy.
3. Add hook observability.
4. Introduce candidate facts and curator verification.
5. Replace alphabetical recall with relevance-based retrieval.
6. Harden recommendation scope and durability.
7. Measure actual outcomes.
8. Expand adapters and synchronization only after those controls are proven through dogfooding.

Neurobase's differentiated value can still be the recommender, but the recommender becomes substantially more credible when it is built on verified facts, isolated scopes, relevant recall, and outcome-based evidence rather than repetition alone.
