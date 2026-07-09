# 2026-07-09 — CI guardrails landed on `main`; Phase 7 rebased + green

_Session log. What shipped today, why, and exactly where to pick up. Not a
contract — the contracts are the spec appendix and the ADRs._

## What shipped to `main`

Two small PRs, both squash-merged, both watched to green on the full matrix:

- **#2 — single-source-of-truth CI gate.** The four checks (`ruff check` ·
  `ruff format --check` · `mypy src tests` · `pytest`) now live once, in
  [`scripts/ci.py`](../../scripts/ci.py). Local dev (`make ci`) and every CI
  matrix job invoke that same script, so the two can't drift — the fix for
  "pushed after running only pytest, went red on ruff/format/mypy." Also added
  an opt-in committed pre-push hook ([`.githooks/pre-push`](../../.githooks/pre-push),
  enable with `git config core.hooksPath .githooks`) and documented the gate in
  README + AGENTS.md. Its first CI run surfaced that **`main` was already red on
  Windows** (13 pre-existing, test-only failures); the PR cherry-picked the two
  existing fixes (`ad15039` + `e454c69`), so this is the PR that finally turned
  `main` green on Windows. Reviewed via the Codex relay
  ([`docs/reviews/2026-07-09-ci-local-gate-guardrails.md`](../reviews/2026-07-09-ci-local-gate-guardrails.md),
  approved, no findings). Rule persisted to Claude memory: run the full gate
  before pushing; watch `gh run` to green after.
- **#3 — off deprecated Node 20.** GH Actions bumped `actions/checkout@v4→v7`
  and `astral-sh/setup-uv@v5→v7` (both `node24`), clearing the Node-20
  deprecation warning. First attempt used `setup-uv@v8`, which failed every job
  at "Set up job" — that action has no moving `v8` tag yet, only exact `v8.x.x`
  releases; `@v7` is already `node24` and keeps our `python-version` +
  `enable-cache` inputs. (Cosmetic: the #3 squash-commit subject still reads
  "→v8"; the diff is correct `@v7`. Not worth rewriting `main`.)

## Phase 7 (MCP) — rebased onto the new `main`, first green run

- `phase-7-mcp-plan` **rebased cleanly** onto updated `main` (no conflicts; the
  two Windows test-fixes it carried are now in `main`, so git auto-dropped them
  — "patch contents already upstream"). 13 MCP commits replayed; force-pushed
  with `--force-with-lease`. Safety backup ref: `backup/phase-7-pre-rebase-20260709`.
- The branch now inherits the shared gate + Node-24 actions. **PR #1's CI is
  green across all six jobs (both Windows included) for the first time** — the
  two prior runs were red only because they inherited `main`'s Windows breakage.
- Phase 7 itself is **code-complete and reviewed**, nothing left to implement:
  `neurobase mcp serve` (stdio, five baseline tools), spec §13 contract,
  ADR-0008, `init`/`doctor`/`uninstall` MCP registration, `core` keyword search.
  Three approved batons (plan · impl · registration), no open findings, no
  `TODO`s in `src/neurobase/mcp/`.

## Resume here (next session)

Phase 7 has **no code work left**. The remaining "Done when" items are
outward-facing / decisions — do NOT do the live steps autonomously:

1. **Live cross-agent MCP demo** (needs the user's real Claude/Codex):
   `claude mcp add` / `codex mcp add`; both agents list + call the tools; Codex
   `/mcp` shows the server and its `resources/list` startup probe succeeds;
   `@`-mention a node in Claude; ask Codex to `memory_search`.
2. **Merge PR #1** to `main` once the demo passes (Router's call).
3. Still queued from earlier phases: the Phase 4/5 live install + cross-agent
   demos against the real `~/.claude` / `~/.codex` configs.

After Phase 7 merges, the next buildable phase is **Phase 8 (the recommender)**.
