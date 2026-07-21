# ADR-0017: Central egress policy — one `authorize_egress()` gate every brain call passes

- **Status:** Proposed
- **Date:** 2026-07-21
- **Resolves:** hardening plan §9 ([plan draft](../notes/2026-07-21-hardening-plan-draft.md), [ratified decisions](../notes/2026-07-21-hardening-decisions.md)); Issue 3
- **Supersedes:** none — builds on the project records of [ADR-0016](0016-store-schema-2-project-records-profiles.md)

## Context

Redaction before a write (`core/redact.py`) is necessary but not sufficient: regex
cannot catch plain-language secrets (client names, account IDs, passwords in prose),
and Neurobase can distill a transcript through a *different* backend than the agent
that produced it — sending content somewhere the user never intended. Today there is
no single place that decides *may this payload go to this backend for this purpose*.
Each brain caller (`curator/engine.py`, the scribes, the recommender miner) invokes
its backend directly.

The hardening plan (§9) calls a per-project/profile egress policy **mandatory P0** —
it is the trust boundary every *later* AI call in the plan routes through. And the
ratified decisions (2026-07-21) turned on a notably AI-forward set of those calls:
the curator **verifier**, scope **classifier**, health **diagnosis**, recommender
**critic**, evaluation **judge**, and mid-session **reranker** all ship, plus
**embeddings**. That is a lot of new egress — which is precisely why it must converge
on **one** gate rather than a check per caller. Only semantic **DLP** was deferred,
and only to an interface (no bundled model).

This ADR depends on [ADR-0016](0016-store-schema-2-project-records-profiles.md):
the `privacy`, `allowed_brains`, and `allow_*` fields it reads live on the project
records that ADR introduces, addressed through the profile-qualified `StoreHandle`.

## Decision

**D32 — Privacy modes: ship `local-only`, define a `default`, reserve the rest.**
The ratified scope (§27 Q3) is a **minimal** first policy schema:

- **`local-only`** — no remote or CLI backend for this project; the store never
  leaves the machine. `authorize_egress` returns *deny* for every backend purpose.
  This is the one *enforcing* mode that adds a genuinely new guarantee.
- **`default`** (the unset value) — still routes through the gate, but *allows the
  currently-configured backends*, preserving today's behavior. It exists so there is
  **no ungated path**: even an unconfigured project's brain calls pass
  `authorize_egress`, which simply returns *allow* for the resolved backend.
- **Reserved, not yet shipped:** the graduated middle modes `same-agent-cli`,
  `approved-cli`, `api-allowed` (§9.3.1). The mode is a closed enum with restrictive
  fallback (§4.5) — an unrecognized value is treated as `local-only`, never as
  `default`.

**D33 — The central gate.** One function every backend call obtains a decision from
first:

```python
@dataclass(frozen=True)
class PayloadProvenance:                  # DERIVED from payload content, not declared
    source_project_slugs: frozenset[str]  # every project whose content is in the payload
    fact_client_ids: frozenset[str]       # distinct fact-level client_ids present

@dataclass(frozen=True)
class EgressRequest:
    purpose: EgressPurpose
    backend: BrainDescriptor
    payload: str                          # the exact string that will be sent
    provenance: PayloadProvenance
    meta: PayloadMetadata                 # sizes, redaction counts — not the raw text

def authorize_egress(store: StoreHandle, request: EgressRequest) -> AuthorizedPayload:
    ...                                   # raises EgressDenied on DENY
```

- **Provenance is *derived from the payload*, never declared by the caller** _(added
  in review — F1 → F4)._ The single payload **assembler** (the corpus/evidence loader
  that packs the bytes) emits `PayloadProvenance` from the *same content it packs* —
  the set of source project slugs and the distinct fact-level `client_id`s actually
  present. A caller cannot under-declare a `local-only` source without also dropping
  its content, because provenance and payload come from one pass. This is what closes
  the confused-deputy gap in the earlier "pass a `projects` list" shape: a hand-passed
  list could omit or soften a source; a derived provenance cannot.
- **The gate resolves records itself; the caller does not choose them** _(F4)._
  `authorize_egress` looks up each `source_project_slug`'s `ProjectRecord` from the
  registry via the handle (ADR-0016 D27). There is no "pass a safer/default record"
  path — the records evaluated are exactly the payload's real sources, and a source
  slug with **no** resolvable record fails closed (`EgressDenied`).
- **Most-restrictive across sources; `client_id` source-of-truth pinned** _(F4)._ The
  decision is the least-authorized source: if any source project is `local-only`,
  denies the backend, or the payload mixes **≥2 distinct non-null `fact_client_ids`**
  (§12.2.2), the whole request is denied. `client_id` lives on **fact metadata**, not
  the project record — it is a field the scope/sensitivity classifier writes
  (hardening §12.2.1, Issue 6, a *Phase-1* ADR). Until that metadata exists,
  `fact_client_ids` is empty and the client-id clause is **inert**; the mode/backend
  rules are fully active in Phase 0. Recording the source-of-truth now keeps the gate
  contract honest about what enforces the rule and when.
- **The token binds to the exact payload** _(F4)._ On allow, the gate returns an
  `AuthorizedPayload` carrying the decision + a hash of `request.payload`.
  `Brain.plan_json` / `text` accepts **only** an `AuthorizedPayload` and re-checks the
  hash against the bytes it sends — so authorize-payload-A-send-payload-B and
  partial-source authorization both fail. The **CI AST check** forbids constructing a
  backend outside the gate module *and* assembling a backend payload outside the
  assembler module, so a gate call with a hand-built, wrong-provenance payload cannot
  compile in the first place.
- `EgressPurpose` is a closed enum — `curate-plan`, `curate-verify`, `scope-classify`,
  `transcript-distill`, `recall-rerank`, `recommend-mine`, `recommend-critic`,
  `evaluation-judge`, `health-diagnose`, `embed`. Every one of the shipping AI calls
  from the ratified decisions maps to exactly one purpose, so a policy can allow
  `curate-plan` while denying `evaluation-judge`.
- The decision reads *only* the resolved project record(s) + backend descriptor +
  payload **metadata/provenance**. It is deterministic — never itself a model call
  (§17.4 forbids an AI call deciding egress). `REQUIRE_LOCAL_DLP` is the seam for D34;
  with no DLP model installed it collapses to `DENY` for a sensitive source and
  `ALLOW` for an all-`default` request.

**D34 — Semantic DLP is an interface-only extension point.** Ship the *contract*, no
model (§27 Q4):

- A `SemanticDLP` protocol: takes local document chunks, returns non-overlapping
  sensitive spans + a document risk label; deterministic code validates offsets
  against the exact input and does the replacement — the classifier **never returns
  modified text** (§9.2.3).
- The classifier, when one exists, **must be local** — an external classifier
  deciding what is safe to transmit defeats the purpose (§9.2.1). The gate returns
  `REQUIRE_LOCAL_DLP` where a sensitive profile *would* benefit; with no provider
  registered that path fails closed. No Ollama dependency, no bundled model in this
  release.

**D35 — `neurobase egress inspect`.** A read-only command that shows, for a
`--purpose` and `--project`, the resolved backend, the privacy mode, bytes before/
after redaction, redaction counts by category, whether DLP ran, and the ALLOW/DENY
reason — with `--show-payload` to print the exact final payload. This is how a user
audits the boundary without reading code, and how the test matrix asserts decisions.

## Consequences

- **Every AI call in the whole plan has one chokepoint.** The seven shipping
  judgment calls plus embeddings all obtain an `EgressDecision` before touching a
  backend; adding an eighth later means adding a purpose, not a new bypass. This is
  the deterministic-enforcement half of "AI proposes, code disposes" (§4.1).
- **`local-only` is real isolation; `default` is honest.** A `local-only` project
  cannot reach a remote API or even a cross-agent CLI. A `default` project behaves
  exactly as today — but *through* the gate, so turning it into `local-only` later is
  a one-field change, not a code change.
- **The `JudgmentService` (§17.2) layers on this.** The shared orchestration wrapper
  (backend selection, structured-output parsing, retries, receipts, prompt
  versioning) calls `authorize_egress` as its first step. This ADR is the
  authorization primitive; the orchestration layer is its own later work.
- **DLP stays swappable.** Because only the interface ships, a future local
  classifier (or a user's own) drops in without touching the gate. The cost is that
  sensitive-content protection beyond regex is *not* present yet — acceptable given
  `local-only` is the shipping enforcing mode and simply blocks egress outright.
- **Spec appendix** updates: §10 gains the privacy-mode enum, the `authorize_egress`
  contract, the purpose list, and the DLP protocol as an extension point; the
  `egress inspect` surface is documented alongside `doctor`/`status`. **ADR proposes;
  spec is law.**
- **Interacts with the curator verifier (Issue 1) and reranker (Issue 2).** A
  `local-only` project that cannot reach a *distinct* verifier backend is exactly why
  the verifier is "preferred-distinct, not required" (ratified) — the gate is what
  makes that fall back cleanly rather than fail.

## Alternatives considered

- **Bundle a local DLP model now** (Issue 3 Solution A, full form) — rejected
  (ratified): pulls an Ollama-class dependency and a whole failure surface into
  Phase 0 for a protection that `local-only` already provides by blocking egress
  entirely. Ship the interface; add a model when a concrete one is chosen.
- **Ship all four privacy modes** — rejected (ratified): the graduated middle modes
  are refinements; `local-only` is the one mode that adds a new guarantee, and a
  `default` that routes through the gate already removes the "ungated path" risk.
  Fewer enum values, less surface to test, room to add the middle later.
- **Per-caller egress checks instead of one gate** — rejected: this is the exact
  shape G1/ADR-0015 diagnosed for the schema guard — protection that holds only where
  an author remembered. With seven-plus AI callers arriving at once, a per-caller
  check would re-arm that footgun immediately.
- **Let the DLP/classifier model also gate egress** — rejected outright (§17.4): a
  model must never decide path authorization or its own egress. The gate is
  deterministic; the model only proposes span labels the gate consumes.
