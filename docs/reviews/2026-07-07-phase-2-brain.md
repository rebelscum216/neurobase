---
slug: phase-2-brain
status: awaiting-review
author: claude
reviewer: codex
branch: phase-2-brain
diff: git diff main...phase-2-brain
created: 2026-07-07
---

# Review: Phase 2 — brain execution backends

## Brief  _(Author — Claude)_

**Intent.** Implement the provider-independent LLM step layer (build-plan
Phase 2, decision D9): a common `Brain` contract with three backends
(`claude_cli`, `codex_cli`, `anthropic_api`), auto-detection + config override,
timeouts/retries, and a `doctor` section reporting which backend resolved and
why. This is the seam the Phase 3 curator's plan/synthesize steps call.

**Scope.** Branch `phase-2-brain` (on `main`@`fa608de`), `git diff
main...phase-2-brain`. Key files:
- `src/neurobase/brain/base.py` — the contract (`plan_json(system, user) ->
  dict`, `text(system, user) -> str`), lenient fence-tolerant JSON parser
  (spec §2 step 3), tuned timeout/retry policy (spec §8: 120s, 1 retry on
  timeout / 5xx / parse failure), the error hierarchy
  (`BrainError`/`BrainUnavailableError`/`RetryableBrainError`).
- `src/neurobase/brain/claude_cli.py` — `claude -p ... --output-format json
  --max-turns 1`; answer = envelope `.result` string (ADR-0002).
- `src/neurobase/brain/codex_cli.py` — `codex exec --json`; answer = last
  `item.completed` event with `item.type == "agent_message"` (ADR-0001).
- `src/neurobase/brain/anthropic_api.py` — Anthropic Messages API via the SDK;
  injectable client; API-key precedence `NEUROBASE_API_KEY` >
  `ANTHROPIC_API_KEY` (spec §10); configured model (default `claude-sonnet-5`).
- `src/neurobase/brain/select.py` — auto-detection in the D9 order plus
  explicit config override; `BrainResolution` dataclass.
- `src/neurobase/cli/__init__.py` — live `doctor` command (replaces its Phase-0
  stub).
- `tests/test_brain_*.py`, `tests/test_cli_doctor.py` — 64 new tests.
- `pyproject.toml` / `uv.lock` — added `anthropic` as a dependency.

**Focus areas.**
- **Retry layering.** Each backend method wraps a single-attempt `_once` in
  `call_with_retry`, and `_once` raises `RetryableBrainError` for
  timeout/5xx/parse-failure vs `BrainError` for hard failures (bad CLI exit,
  4xx, missing binary). The intent: exactly one retry total on transient
  failures, none on hard ones. Does the nesting actually deliver that (no
  double-retry, no retry on 4xx / missing-binary)?
- **CLI-vs-API prompt shape.** CLI backends fold system+user into one prompt
  string (`combine_prompt`), the API backend keeps them in separate slots. Is
  that the right split, and is `combine_prompt`'s `"\n\n---\n\n"` separator
  reasonable (it matches the S5 harness that ADR-0002 validated)?
- **Parse-failure contract.** A `plan_json` parse failure that survives the
  retry escapes as `BrainError`. Phase 3's curator will turn that into
  "abort the pass, leave raws unconsumed" (D9's hard rule). Is the surface
  right for that caller — i.e. is `BrainError` the thing the curator should
  catch, distinct from a *valid-but-empty* plan (which parses fine to
  `{"upserts": [], "tombstones": []}` and is NOT an error)?
- **anthropic_api error classification.** Timeout/connection and 5xx →
  retryable; 4xx → hard `BrainError`. Verified with constructed SDK
  exceptions in tests. Anything miscategorized?

**Known risks / tradeoffs.**
- The API backend deliberately does **not** use structured outputs or a
  thinking config — it prompts for JSON and lenient-parses, same as the CLI
  backends, so all three behave uniformly and the curator's parse-failure
  safety net is identical everywhere. This trades a bit of API-native
  robustness for provider uniformity; a deliberate choice, flag if you
  disagree.
- `doctor`'s detection reports the CLI is present + `--version`, but does not
  positively confirm *logged-in* (that needs a real probe call, which costs
  tokens/latency). The build-plan demo line says "logged in"; I softened to
  "on PATH (version)". Login is confirmed at first real use / the live smoke.
  Acceptable, or should `doctor` do a probe?
- `openai-api` is in the config enum and the D9 auto order but Phase 2 ships
  only three backends; its detector returns unavailable("not implemented
  yet") so auto-detection skips it. Honest, but means a user who sets
  `backend = "openai-api"` gets a clean "not implemented" rather than a
  silent fallthrough.
- Added `anthropic` (+ its deps: httpx, pydantic, etc.) to the core
  dependency set rather than an optional extra, so the API backend is always
  importable/testable. Slightly heavier install for a backend most users
  won't hit first (claude-cli is the D9 default). Flag if you'd prefer an
  extra.

**How to verify.** `uv sync && uv run pytest && uv run ruff check . && uv run
ruff format --check . && uv run mypy src tests`. Live: `uv run neurobase
doctor` prints the resolved backend. The Phase-2 "Done when" live smoke (one
`plan_json` + one `text` through the resolved backend) was run manually
against both claude-cli and codex-cli — both returned a valid plan dict and a
non-empty text line. That smoke script lived in `/tmp`, not the repo (it hits
the real CLIs), so it's not part of this diff.

**Out of scope.** `curator/engine.py` (Phase 3) — the first real consumer of
this contract, still a stub. `openai_api.py` and the ollama seam (post-Phase
2). No live-CLI or live-API tests in the committed suite (they'd need network
+ logged-in CLIs; the unit tests use fake runners / an injected client, and
the live smoke is manual per the "Done when").

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** approve | changes-requested — _one-line rationale._
