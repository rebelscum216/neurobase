# Neurobase — Build Plan v1

Written 2026-07-07 by Fable. Architecture rationale:
`neurobase-architecture-options.md`. Code-level contracts:
`neurobase-spec-appendix.md`.

**This bundle is standalone.** The plan + spec appendix + architecture options
contain everything needed to build — no external code, machines, or private
repos are referenced or required. The spec appendix's contracts were proven by a
prior private implementation; that source is deliberately outside this bundle
(its lineage lives in a local reference map kept with the planning notes) and is
never consulted or copied during the build. When a contract gap surfaces, the
spec appendix gets refined and the build continues from spec.

**Charter change from the prompt:** Neurobase is **open source, not monetized.**
No hosted anything, no telemetry, no pricing tier — ever. The "moat" language in
the options doc becomes: *the recommender is the novel contribution the OSS
ecosystem doesn't have; the memory loop is the well-built commodity underneath it.*

---

## 1. Product framing

Neurobase is an open-source, local-first memory layer that follows a developer
across their coding agents. Claude Code and Codex CLI sessions are captured
automatically (deterministic hooks, no LLM at capture time), folded on a schedule
into a *small* curated fact set by an LLM curator whose mandate is deletion and
supersession, synthesized into a markdown wiki (wikilinked, Obsidian-readable,
git-friendly), and injected back into future sessions scoped by project. On top of
that loop sits the piece nobody ships: a **recommender** that mines the
accumulated cross-agent corpus for recurring patterns and proposes — never
auto-installs — promotions into the standard **SKILL.md** and
**AGENTS.md/CLAUDE.md** formats, learning from which proposals you accept. All of
it runs on the user's machine, on the user's existing agent subscriptions, with
zero cloud dependency and zero telemetry.

**Non-goals (permanent):** hosted backend or sync service; telemetry of any kind;
vector/graph database in the core; brokering anyone's subscription credentials;
per-seat anything. **Non-goals (v1):** agents beyond Claude Code + Codex; teams;
Windows-polish beyond CI-green (best-effort until a maintainer runs it daily);
local-model backend (seam only).

## 2. MVP definition

> **MVP = the cross-agent loop, proven in both directions (end of Phase 5):**
> a Codex session's learnings appear in the next Claude Code session and vice
> versa — capture → curate → node → inject, on a machine where Neurobase was
> installed by `uv tool install neurobase-cli` + per-agent `init --agent
> claude|codex` + `enable`, and nothing else. (The unified one-command `init`
> is Phase 6 polish — the MVP gate allows the per-agent form.)

Everything after (installer polish, MCP, recommender) builds on a loop already
demonstrably working. The recommender (Phase 8) is the headline feature but is
*sequenced* late because it consumes the corpus the loop produces — mitigated by
seeding from the dev machine's existing notes + Claude auto-memory corpus so it
demos on day one of its build.

## 3. Decisions locked at plan time

| # | Decision | Choice | Why / notes |
|---|---|---|---|
| D1 | License | **Apache-2.0** | Patent grant; ecosystem norm (mem0, Letta, Graphiti, cognee). No monetization to defend → no reason for AGPL. *(MIT acceptable if you prefer terseness — veto point.)* |
| D2 | Package name | **PyPI `neurobase-cli`, command `neurobase`** | ✅ Spike done: `neurobase` on PyPI is **taken** (HTTP 200); `neurobase-cli` free (404). Install line: `uv tool install neurobase-cli`. Optionally file a PEP 541 claim on `neurobase` later; don't block on it. |
| D3 | Install mode | **`uv tool install` (persistent shim), not ephemeral `uvx` per-run** | Hooks fire on every session start/end — they must hit a resolved local shim (`~/.local/bin/neurobase`), never a network-resolving ephemeral env. Docs may show `uvx --from neurobase-cli neurobase init` as a try-before-install path. Also `pip install neurobase-cli` works — uv recommended, not required. |
| D4 | Hook command form | **Absolute shim path + subcommand**, e.g. `/Users/x/.local/bin/neurobase hook claude session-end` | Hooks run with minimal PATH; never reference bare `python3`/`node` (not guaranteed present). All `neurobase hook …` entry points: stdin JSON in, deterministic, no LLM, always exit 0. |
| D5 | Store root default | **`~/neurobase/`** (visible), chosen interactively at `init` | Visible-by-default serves the "point Obsidian at it" story; hidden `~/.neurobase` hurts it. Per-project trees underneath: `<root>/projects/<project>/memory/{raw,curated,nodes,.tombstones}` + `index.md` — the proven reference layout, unchanged. Config: `~/.config/neurobase/` on macOS+Linux (XDG, per clig.dev), `%APPDATA%\neurobase\` on Windows — schema in spec §10. |
| D6 | Project identity | **Explicit registry + git-root auto-detect** | `neurobase enable` run inside a repo registers cwd→project (slug from repo dir name, editable) and creates the tree — preserving the reference's opt-in contract (scribes write only where a tree exists). Worktrees resolve to the same project via `git rev-parse --git-common-dir`. Monorepo = one project (v1). Replaces the hardcoded `PROJECT_MAP`. |
| D7 | Cross-agent dedup locus | **The curator** (single place) | Its mandate already is dedup/supersession; Claude+Codex raws for the same project flow into one `curate()` pass. No pre-pass in v1; revisit only if plan-step token cost bites. |
| D8 | Curator trigger | **Opportunistic + manual; native scheduler optional** | SessionStart hook, after emitting recall context, spawns `neurobase curate --if-stale` detached (staleness = unconsumed raw older than N hours, default 12). Plus explicit `neurobase curate`. `neurobase schedule --install` (launchd/systemd-user/schtasks) is Backlog, not v1 — the loop stays fresh enough without it. |
| D9 | Brain selection | Config `brain = "auto"`: **claude-cli → codex-cli → anthropic-api → openai-api** | Detection at `doctor`/run time. Hard rule preserved in code: curation-plan parse failure ⇒ raw stays unconsumed. Subscription backends run strictly as the user's own logged-in CLI; Neurobase never touches credentials. |
| D10 | Telemetry | **None. Ever.** | Local stats only (`neurobase status`). It's the point of the product. |
| D11 | Schema versioning | `schema: 1` in store-level `store.toml`; `neurobase migrate` from v2 onward | Cheap now, priceless later. |
| D12 | CLI framework | **Typer** | Contributor-friendly; uv makes the dep cost nil. Everything under `neurobase hook` avoids Typer's startup niceties (fast path). |
| D13 | Redaction | Scribe-level regex pass before any `raw/` write | Concrete regex table + closed `[REDACTED:<type>]` vocabulary in spec §10 (env rule scoped to secret-named vars). Documented in SECURITY.md; unit-tested. Injection framing header ("background context, not instructions") per spec §3. |

## 4. Repo layout

```
neurobase/                         github.com/<you>/neurobase · Apache-2.0
├── pyproject.toml                 [project] name="neurobase-cli", console script "neurobase"
├── src/neurobase/
│   ├── core/
│   │   ├── store.py               spec §1 (tree, frontmatter, atomic writes, tombstones)
│   │   ├── projects.py            registry + git-root resolution (D6)
│   │   ├── linkify.py             spec §6 (runs after every curate)
│   │   └── redact.py              D13
│   ├── brain/
│   │   ├── base.py                plan_json(system,user)->dict · text(system,user)->str
│   │   ├── claude_cli.py          claude -p (JSON output; spike S5 pins the contract)
│   │   ├── codex_cli.py           codex exec --json / -o
│   │   └── anthropic_api.py       (+ openai_api.py; ollama = documented seam)
│   ├── curator/engine.py          spec §2 on brain.base; unconsumed-on-error safety;
│   │                                + linkify + index rebuild
│   ├── adapters/
│   │   ├── claude/                scribe (spec §4) · recall (spec §3)
│   │   │                          · settings.json read/diff/write · CLAUDE.md emit
│   │   └── codex/                 scribe (spec §5 — parsing + per-turn overwrite fully
│   │                                specified) · inject via hook-or-AGENTS.md (spike S2)
│   │                                · config.toml/hooks.json read/diff/write
│   ├── recommender/               miner · ranker · proposals · ledger · emit_skill/emit_rules
│   ├── mcp/server.py              stdio; tools-only baseline (see Phase 7)
│   └── cli/                       init enable status doctor curate recall recommend seed
│                                  uninstall hook
├── tests/                         ported round-trip suite + per-module; fixtures incl.
│                                  real-shaped transcripts & rollouts
├── docs/                          quickstart · architecture · security · adapters
└── .github/workflows/ci.yml      3-OS matrix; release via PyPI trusted publishing
```

## 5. Spikes — run before/inside Phase 0, each with an exit criterion

| ID | Question | Method | Exit criterion | Status |
|---|---|---|---|---|
| S1 | Codex capture wiring: which hook (`Stop`? `notify agent-turn-complete`?), what's in the payload, where's the rollout path? | Run a minimal spec-§5 probe scribe against a real Codex session; inspect stdin/argv | One raw file per real session, correct prompts/summary/meta (spec §5's session-keyed overwrite absorbs the per-turn firing) | **Narrowed** — `session_start`/`stop` events + trust-hash gate live-verified (spec §7); rollout structure captured (spec §11.2); remaining: turn-completion event's literal name + notify payload fields |
| S2 | Codex injection: does its `SessionStart` hook accept `additionalContext` (or stdout) into model context? | Minimal echo-hook experiment | Either: injection works (adapter mirrors Claude) — or: fall back to managed block in `AGENTS.override.md` (fenced like linkify's, rewritten by recall step) | Open |
| S3 | Clean-machine install | Fresh macOS user acct or container: `uv tool install neurobase-cli` → `neurobase init` with no Python preinstalled | Documented one-liner works; cold-start < 60s | Open |
| S4 | PyPI/GitHub naming | `curl` PyPI JSON endpoints | **DONE:** `neurobase` taken → ship as `neurobase-cli` (D2) | ✅ Closed |
| S5 | `claude -p` JSON contract for the curator (does `--output-format json` + prompt-level JSON demand give parseable plan objects reliably? `--json-schema` usable?) | 10-run harness against the real CLI | Parse success ≥ 9/10 with lenient parser; else prompt/flags adjusted until so | **Narrowed** — envelope captured live (spec §11.3: answer = `.result` string; `--max-turns 1` works); remaining: the 10-run reliability check |
| S6 | Hook latency budget | Time `neurobase hook` cold/warm via `uv tool` shim | Session start+end overhead < 500ms combined warm | Open |

## 6. Phases

Effort unit: one **session** ≈ a focused half-day building with Claude Code.
Total to MVP ≈ 7–9 sessions; to 0.1.0 public ≈ 13–17.

---

### Phase 0 — Repo bootstrap + spikes *(1–2 sessions)*
**Goal:** a skeleton that installs, and the unknowns de-risked.
**Deliverables:** repo (layout above), Apache-2.0 LICENSE, pyproject with console
script, ruff+pytest+pre-commit, 3-OS CI running a hello-world test; spikes S1,
S2, S5, S6 executed and written up as `docs/adr/000x-*.md`.
**Done when:** `uv tool install` from a local build gives a working `neurobase
--help` on macOS; CI green on 3 OSes; every spike has a recorded answer.
**Demo:** `neurobase --help` from a clean shell.

### Phase 1 — Core store, config, projects *(1–2 sessions)*
**Goal:** the storage contract, portable.
**Deliverables:** `core/store.py` (tree per spec §1; `store.toml` per spec §10;
root from config/env); `core/projects.py` (registry + resolution per spec §10);
`core/redact.py` (regex table per spec §10); config module (spec §10 keys);
`neurobase enable/status`; a round-trip pytest suite enforcing every spec §1
invariant, extended (redaction, project resolution, worktrees).
**Done when:** full suite green on the CI matrix; `enable` in a scratch repo
creates the tree; `status` reports projects, raw (consumed/unconsumed), active
facts, nodes.
**Demo:** enable → hand-write a raw file → status shows it.

### Phase 2 — Brain: execution backends *(1 session)*
**Goal:** provider-independent LLM steps honoring the ToS rule.
**Deliverables:** `brain/base.py` contract; `claude_cli` (per S5), `codex_cli`,
`anthropic_api` backends; auto-detection + config override; timeouts, one retry,
clear errors; `doctor` section reporting which backend resolved and why.
**Done when:** unit tests with fake subprocess/API pass; live smoke: one
`plan_json` + one `text` call through whichever backend the machine resolves.
**Demo:** `neurobase doctor` prints e.g. `brain: claude-cli (logged in, claude 2.1.x)`.

### Phase 3 — Curator *(1 session)*
**Goal:** the thinking loop, ported and provable.
**Deliverables:** `curator/engine.py` — the spec §2 sequence
(plan/apply/consume/prune/synthesize), prompts per spec §2.1/§2.2,
**unconsumed-on-parse-failure enforced**; linkify integrated post-pass; index
rebuild; `neurobase curate [--if-stale] [--dry-run]`; deterministic tests via
injected fakes (spec §2 mandates injectable brain steps); one live end-to-end
against a seeded scratch store.
**Done when:** deterministic suite green; live run: raw → curated facts w/
provenance → node regenerated → index + wikilinks present; second run no-ops.
**Demo:** `--dry-run` prints the plan; real run shows the summary dict.

### Phase 4 — Claude adapter: the single-agent loop *(1–2 sessions)*
**Goal:** the full loop on Claude Code, installed by Neurobase itself.
**Deliverables:** `adapters/claude/scribe.py` + `recall.py` per spec §4/§3
(registry-based project resolution, redaction per D13, recall framing +
6000-char cap, plus D8's detached `curate --if-stale` spawn); `neurobase hook
claude session-end|session-start`; minimal `init --agent claude` that shows the
exact settings-JSON diff, asks consent, backs up the original to
`<root>/backups/<ts>/`, writes idempotently (fenced ownership: only hooks
Neurobase created are ever touched), and prints "takes effect next session."
**Done when:** on the dev machine: session A (real work) → exit → raw appears
(redaction verified) → curate → session B opens with the node injected. Empty
sessions write nothing; failure paths exit 0 without wedging teardown.
**Demo:** **"the loop"** — tell Claude a fact in session A; session B knows it.
**Immediately after:** `neurobase enable` on the neurobase repo itself —
dogfooding from day one; the build history becomes the recommender's first
corpus (Phase 8).

### Phase 5 — Codex adapter → ★ cross-agent MVP *(1–2 sessions)*
**Goal:** same loop from Codex, per S1/S2 outcomes.
**Deliverables:** `adapters/codex/scribe.py` implemented from spec §5 (rollout
parsing, IDE-wrapper handling, and the session-keyed per-turn overwrite are all
fully specified there); injection via hook or managed repo-root `AGENTS.override.md`
block per S2 with `.git/info/exclude` hygiene (spec §5); `init --agent codex` (TOML/hooks.json diffs, same consent/backup/
idempotence rules); fixture tests from real rollouts covering the spec §5 cases.
**Done when:** Codex session → one raw (`agent: codex`) → curate folds Claude+
Codex raws into one fact set → **both** next-sessions (Claude and Codex) receive
the node. That is the MVP milestone.
**Demo:** teach Codex something; Claude's next session already knows it.

### Phase 6 — Installer & lifecycle hardening *(1–2 sessions)*
**Goal:** the plug-and-play promise, honest and reversible.
**Deliverables:** unified interactive `neurobase init` (detect agents → choose
store root → enable current repo → per-agent consent diffs → next-session
notice); `doctor` full matrix (uv/shim/PATH, agents+versions, hooks present &
pointing at the right shim, brain resolution, store health, per-check ✓/!/✗ with
named remedies); `uninstall` (restore backed-up configs, leave the store,
`--purge-store` explicit); run spike S3 at the top of this phase, then write the
README quickstart from its clean-machine run.
**Done when:** init→uninstall→init round-trips losslessly on a machine with both
agents; a deliberately broken setup (dead shim path, logged-out agent) is
diagnosed by `doctor` with actionable messages.
**Demo:** screen-recording-grade: two commands to installed-and-working.

### Phase 7 — MCP server *(1 session)*
**Goal:** on-demand memory for any MCP client — the cross-agent surface beyond
the two adapters.
**Deliverables:** `neurobase mcp serve` (stdio, official `mcp` SDK). **Tools
(universal baseline):** `memory_search(query, project?)` (grep/BM25 over
curated+nodes), `memory_read_node`, `memory_list_projects`, `memory_remember(fact)`
(explicit user-directed save → fast-tracked curated fact with provenance
`user-directed`), `recommendations_list`. `resources/list` always answers
validly — node resources listed when dual-exposure is on, empty array otherwise,
never an error (Codex probes it at startup). *Claude-only sugar:* nodes
dual-exposed as resources; `/mcp__neurobase__recall` prompt. `init` offers registration
(`claude mcp add` / `codex mcp add`) with the same consent flow.
**Done when:** both agents list and successfully call the tools live;
tools-only client compatibility verified (Codex `/mcp` shows the server).
**Demo:** mid-session `@`-mention a node in Claude; ask Codex to search memory.

### Phase 8 — Recommender v1 — the novel contribution *(2–3 sessions)*
**Goal:** corpus in → ranked, evidenced, standard-format proposals out; feedback
loop closed.
**Deliverables:**
- `neurobase seed --from-claude-memory --from-dir <path>`: imports existing
  Claude auto-memory dirs and any markdown-notes folder as curated facts
  (provenance `seed:*`). The primary corpus is the **dogfooded build history
  itself** (Neurobase enabled on its own repo since Phase 4 — weeks of real
  sessions by the time Phase 8 starts), topped up by seeding the dev machine's
  own Claude auto-memory. Only personal, non-work content ever enters this
  store.
- **Miner:** brain pass over curated + recent raw across *all* projects/agents;
  candidate types v1: repeated correction (same fix ≥K times), repeated workflow
  (recurring multi-step sequence), repeated instruction (same guidance restated),
  cross-project convention. Each candidate carries evidence links (fact slugs /
  raw filenames).
- **Ranker:** score = recurrence × breadth (sessions·agents·projects) ×
  recency-weight; proposal threshold default: ≥3 occurrences over ≥2 sessions
  (any agent mix).
- **Proposals:** files under `<root>/proposals/<slug>.md` (frontmatter: status
  proposed|accepted|rejected|superseded, type skill|rule, target, evidence,
  scores) — the store pattern reused, human-readable, git-diffable.
- **Review:** `neurobase recommend` (list/show/accept/edit/reject). Accept →
  render **SKILL.md** folder (user or project scope, user's choice) or fenced
  rule block into **CLAUDE.md / AGENTS.md**, shown as a diff, consent, backup —
  never auto-installed. Reject → recorded with optional reason.
- **Ledger + metrics** (`<root>/recommender/ledger.jsonl`): per proposal —
  accepted/rejected/edited; 30-day survival (artifact still present/unmodified —
  checked opportunistically at curate time); recurrence-reduction (does the
  pattern stop recurring post-acceptance?). `status --recommender` prints
  precision, survival, reduction. **This triple is the concrete "improves as
  it's used" metric**; v1 learning = miner deprioritizes candidate-types and
  near-duplicates of rejected proposals (ledger fed into the miner prompt).
**Done when:** seeded corpus yields ≥3 sensible proposals on this machine
(subjective bar: you'd accept ≥1); accept produces a valid SKILL.md that Claude
Code actually loads; reject visibly suppresses similar candidates next run;
ledger metrics render.
**Demo:** the headline reel — `neurobase recommend` proposes a skill from your
real history, you accept, the *next session uses it*.

### Phase 9 — 0.1.0 public release *(1 session)*
**Goal:** shippable open source, honestly documented.
**Deliverables:** README (quickstart, the loop GIF, "how it's different" —
honest comparison table vs basic-memory/Memorix/mem0 acknowledging what they do
well and the redaction + curator + recommender differences); docs/ (architecture
w/ layer contract, SECURITY.md incl. redaction policy + trust-boundary framing,
adapter guide *documenting the adapter seam for third-party agents*);
CONTRIBUTING + issue templates; CHANGELOG; PyPI trusted-publishing release
workflow; tag v0.1.0.
**Done when:** a stranger on a clean machine reaches the Phase-4 demo from the
README alone; `uv tool install neurobase-cli` pulls from real PyPI.
**Demo:** the repo link.

### Backlog (post-0.1.0, in rough order)
SQLite shadow index behind `neurobase index` (FTS5 → sqlite-vec; markdown stays
truth) · native scheduler (`schedule --install`, launchd/systemd/schtasks +
doctor checks) · Ollama backend · third agent via the adapter guide (Gemini CLI /
Cursor — AGENTS.md route makes read-side cheap) · `recall <topic>` explicit pull
· Obsidian starter vault config · basic-memory importer · PEP 541 claim on
`neurobase` · multi-machine story (git-sync the store; docs only, no service).

## 7. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| **Codex capture/injection contracts shift or S1/S2 disappoint** (fast-moving, some docs unverified) | High | Spikes first; parser already proven on real rollouts; AGENTS.override.md fallback needs no hook cooperation; adapter isolation keeps blast radius to one module; `doctor` checks agent versions |
| **Anthropic un-pauses Agent-SDK billing / tightens headless-sub ToS** | High | Brain abstraction (D9): one config line to `anthropic-api`; degradation messaging built in; never brokering credentials keeps us clearly on the right side today |
| **Provenance discipline** — the build must stay spec-derived | Low | The bundle is self-contained by design; the prior private implementation is never consulted or copied. If a contract gap surfaces mid-build, the spec appendix gets refined (its lineage notes live outside this bundle) and the build continues from spec — code never crosses. |
| Curation quality drifts (LLM misfolds facts) | Medium | Unconsumed-on-error preserved; `--dry-run`; tombstone grace period (14d) makes deletions recoverable; active-fact-count trend in `status` as the bloat alarm; curator prompt fixtures as regression tests |
| Hook breakage on agent updates | Medium | Hooks are dumb shims (stdin→store); parsing lives in adapters with fixture suites; `doctor` flags unknown agent versions |
| Secrets reach `raw/` despite redaction | Medium | Regex pass + tests (D13); raw is local-only + gitignored by default; SECURITY.md documents residual risk honestly; easy `neurobase redact --rescan` in Backlog |
| Commodity overlap (basic-memory et al.) reads as "yet another memory MCP" | Medium | Lead every artifact (README, demos) with the recommender + curation story; honest comparison table; emit-standard-formats stance is community-friendly, not competitive |
| Windows reality | Low-Med | CI matrix from Phase 0; schtasks deferred to Backlog; call support level honestly in README |
| Solo-maintainer scope creep | Med | This plan *is* the mitigation: MVP line at Phase 5, backlog discipline, adapter guide to invite contributions |

## 8. Assumptions (flagged for correction)

1. `claude -p` continues drawing on Pro/Max subscription (paused change holds) — recheck at Phase 2.
2. Codex hooks (`Stop`, `SessionStart`, hooks.json) exist as researched in the installed version — S1/S2 verify.
3. Claude Code hook stdin schema (`session_id`, `transcript_path`, `cwd`, `reason`) stays stable — fixture-tested, not guaranteed.
4. Claude auto-memory dir format (`~/.claude/projects/<slug>/memory/`) is stable enough for the Phase-8 seed importer.
5. Rollout `event_msg` channel remains the clean prompt/response source in current Codex builds.
6. You're the only user of the store during v1 — no file-locking beyond atomic writes (two agents ending simultaneously is safe: distinct filenames by agent+session; curator runs are manual/opportunistic, effectively serialized). Revisit if a daemon ever enters the picture.
7. `uv` availability is acceptable as the recommended (not required) path; pip works.
8. Effort estimates assume Claude Code as the builder and `neurobase-spec-appendix.md` as the living spec — the only reference; no prior source is available or needed.
9. The dev machine has Claude Code installed and logged in; Codex CLI availability determines when S1/S2 and Phase 5 can run — install it early if absent.

## 9. Open-source operations

- **License:** Apache-2.0 (D1). Copyright: your name.
- **CI:** GitHub Actions — ubuntu/macos/windows × 2 Python versions; lint (ruff),
  types (mypy, lenient to start), pytest; release job = PyPI trusted publishing
  on tag. No nightly/canary against live agents (can't run them in CI) — `doctor`
  + fixtures carry that load; a `make fixtures-refresh` script documents how to
  re-capture real transcripts/rollouts when agents update.
- **Docs:** README-first; `docs/` plain markdown in-repo (no site generator for
  v1). ADRs for spike outcomes and D-table changes.
- **Community:** CONTRIBUTING (dev setup = `uv sync`, test, fixture policy),
  issue templates (bug/agent-breakage/adapter-request), the adapter guide as the
  designated contribution surface. Single maintainer, no governance theater.
- **Versioning:** SemVer; store schema versioned independently (D11).

## 10. Kickoff checklist (session 1)

1. Create the repo (private until Phase 9); this bundle — the plan, the spec
   appendix, the architecture options — lives at `docs/`.
2. Paste the kickoff prompt from spec-appendix §9 into Claude Code.
3. Scaffold per §4 (package `neurobase-cli`, Apache-2.0, uv, CI hello-world).
4. Verify Codex CLI is installed + logged in; run S1 + S2 (highest-information
   spikes; they shape Phase 5). Run S5 (`claude -p` JSON harness) — shapes Phase 2.
5. Start Phase 1: implement `core/store.py` from spec-appendix §1 + tests.

---

*Companion docs in this bundle: `neurobase-spec-appendix.md` (behavioral
contracts — the law) · `neurobase-architecture-options.md` (researched rationale
for every locked decision).*
