# ADR-0009: VS Code extension as a thin client over a versioned Python protocol

- **Status:** Proposed
- **Date:** 2026-07-10
- **Resolves:** VS Code extension architecture and client boundary
  ([execution plan](../neurobase-vscode-extension-execution-plan.md))
- **Supersedes:** none

## Context

Neurobase may add an optional VS Code extension after the core local-first product
and public-release work. A graphical client is useful for browsing memory, saving
an explicit fact, running curation, and presenting diagnostics. It also creates a
second implementation boundary: TypeScript could begin parsing or mutating the
markdown store, duplicate Python business rules, expose private text through
process arguments, or invent relationships and activity events the store does not
actually record.

The extension must preserve Neurobase's existing guarantees: the Python package
owns behavior, markdown remains authoritative, all operation stays local, there is
no telemetry or hosted dependency, and proposal installation remains explicit and
consent-first. The canonical build plan already assigns Phase 9 to the 0.1.0 public
release, so extension work cannot reuse that phase number.

## Decision

The VS Code extension will be an optional **thin client** built as an independent
post-0.1 workstream under `extension/`:

- The extension host communicates with the installed `neurobase` executable
  through a dedicated, versioned `neurobase client` JSON protocol. It does not
  import Python, parse undocumented markdown formats, or own store mutations.
- The handshake advertises a supported protocol range and capabilities. Optional
  graph and recommendation commands are capability-gated; CLI and extension
  package versions do not need to match.
- Mutations and user content use bounded UTF-8 JSON on stdin. Remembered text never
  appears in argv or routine logs. Responses use stable JSON envelopes, error
  codes, and documented exit behavior.
- Existing business behavior is extracted into shared Python application services
  before being exposed to the client protocol. In particular, MCP and extension
  remember operations share one implementation of redaction, project resolution,
  slug collision handling, `user-directed` provenance, and pinning.
- Webviews send opaque source IDs. Python resolves and canonicalizes them, rejects
  traversal and symlink escape, and returns only recognized files inside the
  selected project memory tree. Webviews never construct filesystem paths.
- Activity displays only events supported by authoritative local data. A richer
  event history requires a separate specified, redacted, append-only local log; it
  cannot be inferred or described as telemetry.
- A graph is derived presentation state, never authoritative state. Stored and
  inferred relationships are labeled separately. The graph ships only after a
  prototype against real stores demonstrates that it is useful.
- An exact curation preview requires a Python-owned preview token plus an input
  fingerprint and stale-state rejection. Two independent LLM runs are not treated
  as preview and apply of the same plan.
- The extension contains no telemetry, analytics, remote backend, CDN dependency,
  API-key setting, or agent credentials. Skill and rule installation continues to
  show the exact diff and require explicit confirmation; Python performs the write
  and backup.

Extension v0.1 is deliberately limited to project health, fact/node browsing,
Python-approved source opening, remember, curate, doctor, truthful recent activity,
and local VSIX packaging. The graph and recommendation studio are later features.

## Consequences

- A documented client protocol and shared Python services must land before the
  extension invokes real Neurobase operations.
- Python remains the only implementation of store, curation, pinning, proposal,
  backup, and consent rules. Other local graphical clients can reuse the protocol.
- Protocol compatibility, input limits, exit behavior, cancellation commit points,
  path confinement, and secret-safe logging become tested public contracts.
- The extension requires its own TypeScript, webview, integration, packaging, and
  cross-platform CI gates. Once stable, the repository's single full gate invokes
  the extension gate as well.
- Some attractive UI features are intentionally delayed. The activity view cannot
  claim recall-injection or pass-start events without new authoritative data, and
  preview cannot promise exact application without the token contract.
- This ADR does not amend D1–D21 or the core store schema. Any new event-log or
  preview-token persistence contract requires a spec change and, if consequential,
  a follow-up ADR.

## Alternatives considered

- **Parse and mutate markdown directly in TypeScript** — fewer subprocess calls,
  but duplicates the behavioral contracts and can drift on malformed files,
  redaction, provenance, pinning, and atomic writes.
- **Expose the existing human CLI through scattered `--json` flags** — initially
  smaller, but produces inconsistent schemas and couples a graphical client to
  presentation-oriented commands and stderr text.
- **Use MCP as the sole extension transport** — reuses an existing interface, but
  does not cover diagnostics, protocol negotiation, safe source resolution,
  curation summaries, or extension-specific bounded presentation models.
- **Run a persistent local daemon or hosted backend** — could reduce process startup
  cost, but adds lifecycle, security, and state complexity and violates the
  no-hosted-dependency architecture. A daemon is not justified for v0.1.
- **Ship the graph in extension v0.1** — visually compelling, but current lineage
  mostly yields provenance, supersession, and broad synthesis-membership edges.
  Its usefulness must be demonstrated before it expands the first release.
