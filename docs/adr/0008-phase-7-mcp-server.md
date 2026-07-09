# ADR-0008: Phase 7 MCP server — shape and decisions

- **Status:** Accepted
- **Date:** 2026-07-08
- **Resolves:** Phase 7 plan decisions D-a…D-e (build plan §6; execution plan
  `docs/notes/2026-07-08-phase-7-mcp-plan.md`; review
  `docs/reviews/2026-07-08-phase-7-mcp-plan.md`)
- **Supersedes:** none

## Context

Phase 7 adds `neurobase mcp serve` — a stdio MCP server exposing memory to any
MCP client, the cross-agent surface beyond the two hook-based adapters. The
build plan fixed the tool baseline and the load-bearing invariant (Codex probes
`resources/list` at startup and drops the server on an error). The execution
plan surfaced five forks (D-a…D-e); the Codex review flagged that the original
`memory_remember` durability claim was wrong (provenance is invisible to the
curator, which can tombstone/supersede any fact). Those had to be resolved
before the contract was authoritative.

## Decision

- **D-a — search:** grep + term-frequency scoring in `core/search.py` (slug/name
  weighted over body). Pure, offline, reusable. BM25/FTS index stays backlog.
- **D-b — `memory_remember` durability:** user-directed facts are **pinned**.
  The curator payload marks them `"pinned": true`, the plan prompt forbids
  changing them, and the apply pipeline enforces it **deterministically** (pinned
  slugs dropped from upserts, supersessions, and tombstones). This is a real
  curator change (§2), chosen over "accept curator authority" so an explicit
  "remember this" cannot silently vanish.
- **D-c — read scoping:** omitted `project` on read tools searches **all**
  registered projects (the server can't trust one session cwd for reads); the
  write tool resolves a project from launch cwd, else an explicit argument.
- **D-d — dual-exposure:** node resources + the `recall` prompt are **off by
  default** (`[mcp] expose_resources`). Tools are the universal baseline;
  resources are Claude-only sugar. `resources/list` returns `[]` when off.
- **D-e — redaction:** `memory_remember` runs the §10/D13 redaction pass before
  writing, same as the scribes.
- **SDK pin:** `mcp` is **exact-pinned** (`mcp==1.28.1`) so `doctor`/CI can flag
  surface drift; a lower bound would let clients silently pick up a new surface.

## Consequences

- Spec appendix gains **§13 (MCP server contract)** and a **Pinned facts** rule
  in §2; `core/config.py` gains `[mcp]`. `core/search.py` is new and reusable by
  the Phase 8 miner.
- The curator now honors a pin that originates outside it — a small coupling
  from the MCP write path into the §2 pipeline, enforced in code + prompt + tests.
- The `resources/list` invariant is covered by a three-state regression test and
  verified over a live stdio session.
- Still open (tracked in the execution plan): `init`/`doctor`/`uninstall`
  registration (WS-D), and live multi-agent integration (Claude `@`-mention +
  Codex `/mcp`) as the Phase-7 done-when demo.

## Alternatives considered

- **Accept curator authority over user-directed facts (D-b option 1)** — simpler,
  no curator change, but an explicit user save could be folded away on a later
  pass. Rejected: violates the human-authority principle.
- **Lower-bound `mcp>=`** — flagged by the review as inconsistent with the
  drift-pinning intent; an isolated `uv tool` install makes an exact pin cheap.
- **Low-level `Server` over FastMCP** — more control, far more boilerplate;
  FastMCP already guarantees a valid `resources/list`, so it wins.