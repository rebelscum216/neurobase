# Phase 7 — MCP server: execution plan

_2026-07-08 — a working plan decomposing build-plan Phase 7 (`neurobase mcp
serve`) into ordered, checkable work. Not a contract: the contract lands in the
spec appendix (see Workstream A). Supersede this note's conclusions there once
locked._

Source of truth for scope: [build-plan §6 Phase 7](../neurobase-build-plan.md).
This note only sequences and de-risks that scope; it does not expand it.

---

## 1. Goal (verbatim intent)

On-demand memory for **any** MCP client — the cross-agent surface beyond the two
hook-based adapters. A single stdio server, built on the official `mcp` SDK,
exposing a small universal tool baseline plus Claude-only sugar. The hard
constraint that shapes everything below: **`resources/list` must always answer
validly** (empty array, never an error) because Codex probes it at startup.

**Done when** (from the plan): both agents list and successfully call the tools
live; a tools-only client (Codex `/mcp`) shows the server. **Demo:** `@`-mention
a node in Claude mid-session; ask Codex to search memory.

Sizing: the plan budgets **1 session**. Realistic if Workstreams A–D stay tight
and we resist pulling Phase 8 forward (see §7).

---

## 2. Current state (what exists to build on)

- [`src/neurobase/mcp/__init__.py`](../../src/neurobase/mcp/__init__.py) — a
  docstring stub only. Greenfield module.
- CLI `mcp` is a **stub command** registered at
  [`cli/__init__.py:544`](../../src/neurobase/cli/__init__.py) via `_make_stub`
  ("Run the MCP server exposing memory tools to any client"). Phase 7 replaces it
  with a real `mcp serve` subcommand.
- Store API to reuse (no new persistence needed):
  `list_curated`, `list_raw`, `read_doc`, `write_node`, `read_node` bodies via
  `_node_bodies` in `adapters/recall_common.py`, `upsert_curated(...,
  provenance=...)`, `resolve_root`.
- Projects API: `projects.load_registry`, `resolve_project`, `slugify`.
- `recall_common.build_context` already assembles nodes for a project — reuse
  its assembly/cap logic, don't reimplement.

### Gaps that must be filled first (not yet in the tree)

1. **No search primitive.** There is no grep/BM25 over the store anywhere in
   `core/`. `memory_search` needs a real implementation (Workstream B).
2. **No `mcp` SDK dependency.** `pyproject.toml` deps are typer, pyyaml,
   tomli-w, anthropic, keyring — `mcp` is absent and not in the venv. Add it.
3. **No MCP section in the spec appendix.** The spec (`neurobase-spec-appendix`)
   runs §1–§11 and stops before MCP. Per docs convention ("when code and spec
   would diverge, change the spec first"), the contract gets written **before**
   the server (Workstream A).
4. **No dual-exposure config flag.** `core/config.py` has no `[mcp]` section.

---

## 3. Decisions to lock before coding (open questions)

These are genuine forks; resolve them at the top of the session (fold into the
spec section) rather than discovering them mid-build.

| # | Question | Leaning / default |
|---|---|---|
| D-a | `memory_search` ranking: pure substring/grep, or BM25? | Ship **grep + simple term-frequency scoring** in v1; BM25 is backlog (SQLite FTS5 line already there). Keep the interface stable so the impl can swap. |
| D-b | `memory_remember` durability: is a user-directed fact protected from the curator, or a normal curated fact the curator may later fold/tombstone? | **Correction (Codex F1):** provenance gives it *no* protection. The curator sees only `{slug, body}` — provenance is not in `_facts_payload` ([`curator/engine.py:62`](../../src/neurobase/curator/engine.py)) — and it can supersede or tombstone any slug on a later pass ([`engine.py:187`](../../src/neurobase/curator/engine.py)); `upsert_curated` merges provenance only on a same-slug re-upsert and overwrites the body wholesale, `agent_last=curator` ([`store.py:252`](../../src/neurobase/core/store.py)). So a remembered fact persists only *because the curator's plan omits unchanged facts*, not by protection. **Real fork to lock:** (1) treat it as an ordinary curated fact, accept curator authority (simplest, but "remember this" can silently vanish); or (2) make `user-directed` provenance **prompt-visible** so the curator treats it as pinned — a spec §2 curator-prompt change. **Recommend (2)**: honoring an explicit user save matches the human-authority principle. Either way, write to `curated/` with a slugified-first-line slug (dedupe suffix) + provenance `user-directed`, and **test the chosen contract** end-to-end (remember → curate pass → assert survival/supersession). |
| D-c | `memory_search`/`read_node` project scoping when `project?` omitted: search all projects, or resolve from server CWD? | Omitted ⇒ **all projects** (server has no session CWD to trust). Explicit `project` filters. |
| D-d | Dual-exposure default: resources on or off by default? | **Off** by default; `[mcp] expose_resources = true` opts in. `resources/list` still returns `[]` validly when off. |
| D-e | Redaction on `memory_remember` input — reuse `core/redact` before write? | **Yes**, same D13 pass the scribes use. A user pasting a secret into `remember` must not persist it raw. |

---

## 4. Workstreams (ordered)

### A. Spec contract first  _(spec appendix §12, new)_
Write the MCP contract as the authoritative section **before** implementing:
- Transport: stdio, official `mcp` SDK, server name `neurobase`.
- Each tool: name, JSON input schema, output shape, error/empty behavior.
- The invariant: `resources/list` returns a valid (possibly empty) array in
  every configuration; **never** raises. `tools/list` likewise stable.
- Dual-exposure semantics + the `[mcp]` config keys.
- Claude sugar: node→resource mapping and the recall prompt.
- If any behavior here is a real decision (D-a…D-e), also log an **ADR** and
  link it, per the notes→ADR promotion rule.

### B. Search primitive  _(`core/` — reusable, not MCP-specific)_
- Implement search over `curated/` bodies + `nodes/` bodies for a project (or
  all projects). Return ranked hits with: project, slug/name, kind
  (curated|node), a snippet, and a score.
- Deterministic, no LLM, no network. Pure function over the store on disk.
- Unit-tested independently of the server so Phase 8's recommender can reuse it.

### C. The server  _(`mcp/` — the five tools + resources + prompt)_
Baseline tools (universal, must work on a tools-only client):
- `memory_search(query, project?)` → ranked hits (Workstream B).
- `memory_read_node(project, name)` → node body (reuse recall assembly).
- `memory_list_projects()` → registry projects with counts.
- `memory_remember(fact, project?)` → fast-tracked curated fact, provenance
  `user-directed`, redacted first (D-b/D-e).
- `recommendations_list(project?)` → proposals under `<root>/proposals/`.
  **Phase 8 owns proposals**; here it returns `[]` validly when the dir is
  absent. Wire the read path, don't fake data.

Resources + prompt:
- `resources/list` — nodes exposed as resources **iff** dual-exposure on; else
  `[]`. This is the Codex-probe safety net; test it in the off state explicitly.
- `resources/read` for a node URI.
- Claude sugar: `/mcp__neurobase__recall` prompt returning recalled context.

### D. `init` registration  _(extend the installers)_
- `init` offers `claude mcp add` / `codex mcp add` under the **same consent /
  diff / backup flow** the Phase 4–6 installers already use
  (`adapters/*/install.py`, `_unified_diff` in the CLI). Do not shell out
  silently — show the change, get consent, back up.
- `doctor` gains an MCP check: is the server registered with each detected
  agent, and does `mcp serve` start.
- `uninstall` removes the registration it added.

### E. Wiring + deps
- Replace the `mcp` stub command with a real `serve` subcommand
  (`neurobase mcp serve`, stdio).
- Add the `mcp` SDK to `pyproject.toml` deps as an **exact pin** `mcp==<x.y.z>`
  (not a lower bound — see §7 SDK-drift risk); record the version in the ADR;
  refresh `uv.lock`.
- `[mcp]` config dataclass in `core/config.py` (`expose_resources`, etc.).

---

## 5. Tool contract sketch (fold into spec §12)

| Tool | Input | Output | Empty / error rule |
|---|---|---|---|
| `memory_search` | `query: str`, `project?: str` | list of `{project, name, kind, snippet, score}` | no hits ⇒ `[]`; bad project ⇒ `[]`, not error |
| `memory_read_node` | `project: str`, `name: str` | `{project, name, body}` | missing ⇒ structured "not found", not a crash |
| `memory_list_projects` | — | list of `{project, curated_count, node_count}` | no store ⇒ `[]` |
| `memory_remember` | `fact: str`, `project?: str` | `{project, slug, path}` | redact → write; empty fact ⇒ validation error |
| `recommendations_list` | `project?: str` | list of proposal summaries | no `proposals/` ⇒ `[]` |

`resources/list` → node resources or `[]`. **Never raises**, in any config.

---

## 6. Test plan

- **Unit:** search ranking/scoping (B); `memory_remember` writes correct
  frontmatter (`provenance: [user-directed]`, redacted body); each tool's
  empty-path returns `[]`/structured-miss not an exception.
- **Invariant test:** `resources/list` returns a valid array with dual-exposure
  **off**, **on-but-no-nodes**, and **on-with-nodes**. This is the Codex-probe
  regression guard — make it loud.
- **Integration (live, the done-when):**
  1. `neurobase mcp serve` over stdio; drive `tools/list` + each `tools/call`
     with a raw MCP client harness.
  2. **Claude:** `claude mcp add`, then `@`-mention a node + call the recall
     prompt in a real session.
  3. **Codex:** `codex mcp add`, `/mcp` lists the server, `memory_search`
     returns hits. Confirm Codex's startup `resources/list` probe succeeds.
- `doctor` reports the server registered + startable for both agents.

---

## 7. Risks / watch-items

- **Scope creep from Phase 8.** `recommendations_list` is the seam. Wire the
  read path and return `[]`; do **not** build the miner/ranker here. If tempted,
  stop — that's a separate 2–3 session phase.
- **`memory_remember` vs curator authority.** The curator can supersede/tombstone
  any slug and never sees provenance, so a `user-directed` fact is *not* protected
  by default (Codex F1). Lock D-b's fork — pin via a prompt-visible provenance
  (spec §2 change) or accept curator authority — and test the chosen contract
  before calling this done.
- **Codex `resources/list` probe.** If this ever errors, Codex may drop the
  whole server. The invariant test above is non-negotiable.
- **SDK surface drift.** Exact-pin `mcp==<x.y.z>` in deps (§4.E) — a lower bound
  would let CI/users silently pick up a newer, differently-shaped SDK. Record the
  version in the ADR so `doctor`/CI can flag a mismatch later.

## 8. Explicitly out of scope (defer)

BM25/FTS5 index (backlog `neurobase index`), the recommender itself (Phase 8),
HTTP/SSE transport (stdio only), and any write path beyond `memory_remember`.

---

_When A locks, promote the tool contract to spec appendix §12 + an ADR, and this
note becomes the evidence trail._
