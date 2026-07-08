# AGENTS.md — operating guide for Neurobase

> This is the entry point for any coding agent (or human) working in this repo.
> Read it first. It is intentionally short and high-signal; it points to the
> authoritative documents rather than duplicating them.
>
> Neurobase's own headline feature *emits* AGENTS.md files — so treat this one as
> a worked example of the format: a tight map, not a manual.

## What this project is

Neurobase is an **open-source, local-first memory layer that follows a developer
across their coding agents** (Claude Code + Codex CLI in v1). It captures sessions
deterministically (hooks, no LLM at capture time), folds them on a schedule into a
*small* curated fact set via an LLM curator whose mandate is deletion and
supersession, synthesizes a wikilinked markdown wiki (Obsidian-readable,
git-friendly), and injects that memory back into future sessions scoped by project.
On top of the loop sits the novel piece: a **recommender** that mines the
cross-agent corpus for recurring patterns and proposes — never auto-installs —
promotions into standard **SKILL.md** and **AGENTS.md/CLAUDE.md** formats.

Everything runs on the user's machine, on their existing agent subscriptions, with
**zero cloud dependency and zero telemetry — permanently.**

## The three canonical documents (read in this order)

| Document | Role | How to treat it |
|---|---|---|
| [docs/neurobase-build-plan.md](docs/neurobase-build-plan.md) | The phased plan | **Follow it.** Work phase by phase; each phase's "done when" gates the next. |
| [docs/neurobase-spec-appendix.md](docs/neurobase-spec-appendix.md) | Behavioral contracts | **Law.** `MUST` = a contract tests enforce. Implement *from this spec*. |
| [docs/neurobase-architecture-options.md](docs/neurobase-architecture-options.md) | Researched rationale | Consult when a decision needs its "why." Discovery input, not a build order. |

The [docs/ index](docs/README.md) maps everything, including ADRs and working notes.

## Non-negotiable build principles

1. **Spec is law; tests enforce it.** Every `MUST` in the spec appendix gets a
   test. Tuned defaults come from spec §8, on-disk formats from §10, and §11's
   captured fixtures are the ground truth for parsers — write fixture tests from
   them on day one.
2. **Provenance discipline (critical).** The contracts were extracted from a prior
   private implementation that is **not in this repo and must never be consulted or
   copied.** Build *only* from the spec. If a contract gap surfaces mid-build,
   refine the spec appendix and continue from spec — source code never crosses the
   boundary.
3. **Fail-safe by default.** Hooks are deterministic, take stdin JSON, run no LLM,
   and **always exit 0** — never wedge an agent's session teardown or startup. On
   any error, capture nothing / inject nothing rather than crash.
4. **Optimize for deletion.** The curator's job is a *small, current, non-redundant*
   fact set — merging and supersession over accumulation. This is the load-bearing
   quality bar; the markdown substrate itself is a commodity.
5. **No telemetry. Ever.** Local stats only. It is the point of the product.
6. **Consent-first for anything outside our own files.** Installing hooks or writing
   agent config shows the exact diff, asks consent, backs up originals, and is
   idempotent + reversible. (clig.dev's rule; spec §7.)

## Repo layout (target — see build-plan §4 for the full tree)

```
neurobase/
├── AGENTS.md                 ← you are here
├── README.md                 ← project front door
├── LICENSE                   ← Apache-2.0 (decision D1)
├── pyproject.toml            ← package "neurobase-cli", command "neurobase"
├── src/neurobase/            ← cli/, core/{store,projects,redact,config,linkify},
│                                brain/{base,claude_cli,codex_cli,anthropic_api,select},
│                                curator/engine, adapters/claude/{scribe,recall} (live)
│                                · adapters/codex/ recommender/ mcp/ (stubs)
├── tests/                    ← Phase 0 smoke + Phase 1/2/3/4 suites; spec-§11 fixtures land per phase
├── docs/                     ← canonical docs, ADRs, notes, code-review relay + reviews
├── .claude/skills/           ← project skills (e.g. xcode-review, the Author role)
└── .github/workflows/ci.yml  ← 3-OS × 2-Python matrix (lint, format, types, tests)
```

`core/` (Phase 1 + `linkify` Phase 3), `brain/` (Phase 2), `curator/` (Phase
3), and `adapters/claude/` (Phase 4: scribe + recall) are real. `adapters/codex/
recommender/ mcp/` are still docstring-only stubs, each naming the spec section
and phase that will fill it in. Replace a stub with real code when its phase
lands.

## Current state

- **Phase:** 0 — **fully done**, spike gate included. Repo bootstrap: installable
  package, live `cli` with an honest stubbed command surface, smoke tests,
  ruff/mypy/pytest, 3-OS CI all pass. All four gating spikes are closed:
  [ADR-0001](docs/adr/0001-codex-capture-wiring.md) (S1),
  [ADR-0005](docs/adr/0005-codex-injection-confirmed.md) (S2 — supersedes
  [ADR-0004](docs/adr/0004-codex-injection-fallback.md), an initial wrong
  conclusion Codex's own review caught and reversed),
  [ADR-0002](docs/adr/0002-claude-cli-json-reliability.md) (S5),
  [ADR-0003](docs/adr/0003-hook-latency-budget.md) (S6). S2's confirmed
  answer: Codex's `SessionStart` hook output **does** reach the model, as a
  `developer`-role input message — injection mirrors the Claude adapter, per
  spec §5/§3. (S3, clean-machine install, is tracked in the build-plan spike
  table but isn't part of Phase 0's closing gate — see build-plan §6 Phase 0
  deliverables. Not started.)
- **Phase 1 — core store, config, projects: done.** `core/store.py` (tree,
  YAML-frontmatter document format, atomic writes, raw/curated/nodes/index per
  spec §1, including the Codex per-turn overwrite trick and the
  consumed-mutability rule), `core/projects.py` (registry + git-common-dir
  resolution incl. worktrees + slugify, spec §10/D6), `core/redact.py` (the
  full D13 table), `core/config.py` (spec §10 keys, §8 defaults). Live
  `neurobase enable`/`status` commands. 57 tests (round-trip + CLI
  integration), ruff/mypy/pytest all green.
- **Phase 2 — brain: execution backends: done.** `brain/base.py` (the
  `plan_json`/`text` contract, lenient fence-tolerant JSON parse, 120s timeout
  + 1-retry policy per spec §8), three backends behind it — `claude_cli`
  (ADR-0002), `codex_cli` (`codex exec --json`, ADR-0001), `anthropic_api`
  (SDK, injectable client) — and `brain/select.py` (auto-detection in the D9
  order claude-cli → codex-cli → anthropic-api → openai-api, config override).
  Live `neurobase doctor` reports which backend resolved and why. All three
  run as the user's own logged-in CLI / their own API key (D9 ToS rule);
  Neurobase never touches credentials. 121 tests total; live smoke (one
  `plan_json` + one `text`) verified through both claude-cli and codex-cli.
- **Phase 3 — curator: done.** `curator/engine.py` runs the full spec §2 loop
  (plan → apply upserts/supersession → explicit tombstones → consume raws →
  prune → synthesize node → index → linkify), with the hard rules enforced:
  a plan that won't parse aborts and leaves raws unconsumed (D9); a
  valid-but-empty plan IS consumed; node-synthesis failure after consumption
  is `partial` (self-heals). Brain is injected (testable with fakes, no
  network). `core/linkify.py` (spec §6): idempotent `[[wikilink]]` lineage
  blocks in curated/nodes bodies, frontmatter byte-for-byte preserved,
  raw/.tombstones never touched. Live `neurobase curate [--if-stale]
  [--dry-run] [--resynth]`; `status` shows the fact-count trend from
  `.curator-log.jsonl`. 158 tests total; full "Done when" verified live
  (2 raws from 2 agents → 2 deduped facts w/ provenance → node + index +
  wikilinks → second run no-ops).
- **Phase 4 — Claude adapter (scribe + recall + hook): done.** `adapters/
  claude/scribe.py` (spec §4): SessionEnd transcript parse (§11.1 fixture —
  sidechain/tool_result/noise skipped, last-non-empty assistant summary,
  §8 bounds), D13 redaction before write, opt-in (write only if the project
  tree exists), empty-capture writes nothing. `adapters/claude/recall.py`
  (spec §3): SessionStart nodes → `additionalContext` with the proven framing
  header, 6000-char cap (drop whole trailing nodes), detached
  `curate --if-stale` spawn (D8), fail-safe (any error ⇒ emit nothing). Live
  `neurobase hook claude session-end|session-start` (stdin JSON, **always
  exits 0** — never wedges teardown). The loop is verified live through the
  installed shim (SessionEnd raw → curate → SessionStart emits the injected
  context). `neurobase init --agent claude [--user] [--yes]` is now live
  (`adapters/claude/install.py` + `core/backups.py`, spec §7/§10): it shows the
  exact settings.json diff, asks consent, backs the original up under
  `<root>/backups/<ts>/` with a manifest, then writes atomically — fenced
  ownership (spec §7) means only entries whose command invokes a `neurobase`
  executable's `hook` subcommand (`<shim>/neurobase hook`, path-anchored) are
  ever created/replaced/removed, so every other key and hook is preserved, and
  a malformed settings.json is refused rather than clobbered. 207 tests.
  **Still deferred** (needs the user's real Claude config + live sessions): the
  in-vivo session-A→session-B demo of the installed hooks.
- **Phase 5 (core) — Codex adapter → ★ cross-agent MVP: done (code + tests).**
  `adapters/codex/scribe.py` (spec §5): rollout JSONL parse (§11.2 fixture —
  `session_meta`/`user_message`/`agent_message`; `response_item`/`turn_context`/
  token channels ignored), VS Code IDE-wrapper split (`## My request for Codex:`
  → prompt kept; preceding block kept once as a ≤800-char `## Files in focus
  (IDE)` section), consecutive-duplicate-prompt skip (thread_rolled_back),
  D13 redaction, opt-in, empty-skip, and the **per-turn overwrite trick** —
  `captured_at = session-start timestamp` so every turn resolves to one raw
  file, last-turn-wins (falls back to a fresh filename if the raw was already
  consumed mid-session). `discover_rollout` (newest `rollout-*.jsonl` by mtime +
  session-id cross-check) for the `notify` path that carries no rollout path
  (§11.4). Injection is agent-agnostic and now lives in
  `adapters/recall_common.py` (extracted from the Claude adapter, both re-export
  it) — Codex `SessionStart` output reaches the model identically (ADR-0005).
  Live `neurobase hook codex session-start|stop|notify` (stdin JSON for
  stop/start, argv JSON for notify; **always exits 0**). The MVP "Done when" is
  covered by `tests/test_cross_agent.py` (a Claude raw + a Codex raw fold into
  one fact set → **both** next-sessions recall the node) and the codex `stop`
  capture is verified live through the installed shim. `neurobase init --agent
  codex [--user] [--yes]` is live: it writes CamelCase `SessionStart`/`Stop`
  hooks to `hooks.json` using absolute shim command strings, and for project
  scope surgically merges `hooks = ".codex/hooks.json"` +
  `trust_level = "trusted"` into `~/.codex/config.toml` while preserving
  unrelated comments/tables/keys. ADR-0006 records the live spike confirming
  Codex tokenizes string commands, sends stdin JSON, and re-prompts trust after
  hook edits; init prints that approval reminder. 266 tests. **Still deferred**
  (needs the user's real Codex config): the live installed `init --agent codex`
  run and full in-vivo cross-agent demo.
- **Naming (decision D2):** PyPI package = `neurobase-cli`, command = `neurobase`
  (`neurobase` is taken on PyPI). The npm `neurobase` name is a *defensive
  reservation only* — this is a **Python** project; `package.json`/`index.js` are a
  placeholder holding that name, not part of the build.
- **License (D1):** Apache-2.0.

## Dev workflow

```bash
uv sync                     # install deps into a managed venv (bootstraps Python)
uv run neurobase --help     # run the CLI from the dev env
uv run pytest               # run the suite — the contract enforcer
uv run ruff check .         # lint
uv run ruff format .        # format
uv run mypy src tests       # types (lenient to start)
uv run pre-commit install   # optional: enable the pre-commit hooks
```

CI runs `ruff check`, `ruff format --check`, `mypy src tests`, and `pytest` on a
3-OS × 2-Python matrix — keep all four green. To validate the installed shim
(not just the dev env): `uv tool install .` then `neurobase --help`.

## Where to put things

- **A new design decision or spike outcome** → an ADR in
  [docs/adr/](docs/adr/README.md) (copy `0000-template.md`). The plan mandates ADRs
  for every spike (S1–S6) and every change to the decision table (D1–D13).
- **Scratch thinking, investigation logs, a running scratchpad** →
  [docs/notes/](docs/notes/README.md). Not a contract; date your notes.
- **A change to a behavioral contract** → edit the spec appendix (it's the living
  spec) *and* note the change in an ADR. Never let code and spec diverge silently.
- **Real transcript/rollout fixtures** → `tests/fixtures/` (once tests exist),
  shaped from spec §11. Sanitize every captured value.
- **A code-review handoff** → follow the [code-review relay](docs/code-review-relay.md);
  the baton is a file in [docs/reviews/](docs/reviews/README.md).

## Code review relay (Claude ⇄ Codex)

This repo has a **defined cross-agent review process**: Claude authors, Codex
reviews independently, findings come back, Claude resolves. The full protocol,
roles, and reviewer checklist are in
[docs/code-review-relay.md](docs/code-review-relay.md) — that file is the single
source of truth; this section and the Claude `xcode-review` skill are pointers.

- **If you are Codex, you are the Reviewer.** When the user points you at a review
  file under `docs/reviews/`: read the brief, then **run the diff and review the
  actual code**, verifying the brief's claims rather than trusting them. Assess
  against the checklist in the protocol doc (correctness · spec adherence — a
  `MUST` violation is a **blocker** · tests · security · simplicity · provenance).
  Append findings (severity · `file:line` · issue · suggested direction) and end
  with a verdict (`approve` | `changes-requested`). **Do not fix** — that's the
  Author's job.
- **If you are Claude, you are the Author** — the `xcode-review` skill
  (`.claude/skills/xcode-review/`) drives your half.
- **Keep Author and Reviewer as separate sessions.** The independent perspective is
  the entire point of the relay.

## Conventions

- **End each response with a suggested next action.** Whichever agent you are,
  close by telling the user what you'd do next. The one exception: mid a
  code-review round-trip (you've just handed off and are waiting on the other
  agent's findings/verdict) — there the next step is obviously to wait, so skip
  it.
- **Commits:** imperative, scoped ("curator: enforce unconsumed-on-parse-failure").
  Reference the phase or decision when relevant.
- **Slugs** (projects, facts, nodes) match `^[a-z0-9-]+$` — enforced in code (spec §1).
- **Markdown docs:** wrap prose ~80 cols to stay git-diff-friendly, matching the
  existing docs.
- **Secrets never land in the repo or the raw store.** Redaction runs before any
  `raw/` write (spec §10 table); the store is gitignored by default.

---

*If something here conflicts with the spec appendix, the spec wins — and fix this
file.*
