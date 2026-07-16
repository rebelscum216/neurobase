---
slug: webui-phase1-suggestions
status: awaiting-review
author: claude
reviewer: codex
branch: feat/webui-phase1-suggestions
diff: git diff main...HEAD
created: 2026-07-16
---

# Review: Web UI Phase 1 — Suggestions review surface + its law and plans

## Brief  _(Author — Claude)_

**Intent.** Ship the first web presentation layer: `neurobase ui`, a
loopback-only Starlette+Jinja2 app for reviewing recommender proposals —
list/detail with metrics, accept preview (diff) → CSRF-gated commit, reject,
edit — installing through the same choreography as the CLI. Alongside the code:
promote the surface to law (spec §14 + ADR-0012), record two live-smoke
findings as known-gaps G2/G3, and land the two working plans for what comes
next (session→fact provenance hardening; the app-shell UI phases).

**Scope.** Branch `feat/webui-phase1-suggestions`, `git diff main...HEAD`
(5 commits, 24 files, +2740/−32). Key files:
- `src/neurobase/recommender/install.py` — NEW: `prepare_install` /
  `commit_install` extracted from `cli/recommend_accept` (the D-1 prerequisite).
  The CLI wrapper preserves messages/exit codes; `tests/test_cli_recommend.py`
  is unmodified.
- `src/neurobase/webui/` — NEW package (peer of `cli/`): `app.py` (build_app,
  serve hard-pinned to 127.0.0.1), `security.py` (same-origin + per-process
  CSRF middleware, gates every POST before routing), `routes.py` (6 routes),
  `templates/` (server-rendered, zero JS).
- `src/neurobase/cli/__init__.py` — `recommend_accept` now delegates to the
  install service; new lazy-importing `ui` command (`--root`, `--port`).
- `docs/neurobase-spec-appendix.md` §14 + `docs/adr/0012-webui-surface.md` —
  the surface's behavioral contract.
- `docs/known-gaps.md` — G2 (accepted-state/disk drift, no revert path),
  G3 (emitter frontmatter doubling; `description` from `candidate_type`).
- `docs/notes/2026-07-15-webui-phase1-plan.md`,
  `docs/notes/2026-07-16-provenance-plan.md`,
  `docs/notes/2026-07-16-webui-app-shell-plan.md` — the executed plan and the
  two forward plans (heavily `file:line`-grounded; both were adversarially
  fact-checked against the code before landing, but fresh eyes welcome).
- `pyproject.toml` / `uv.lock` — `jinja2>=3.1`, `uvicorn>=0.30` as direct base
  deps.

**Focus areas.**
1. `security.py` — the CSRF/same-origin middleware. Especially the
   body-before-form ordering (`await request.body()` before `request.form()` so
   BaseHTTPMiddleware replays the POST body downstream) and whether any
   mutating path can slip the gate (it dispatches on method == POST only).
2. Install parity — does the web accept path preserve every §12 MUST
   (status-guard before render/diff/write, backup, atomic write, ledger
   `installed_hash`, no-op short-circuit with no ledger event, POST re-running
   `prepare_install` fresh)?
3. The D-1 extraction — behavior preservation in `recommend_accept`
   (messages/exit codes/short-circuits byte-for-byte).
4. Spec §14 / ADR-0012 accuracy — does the written law match the shipped code
   exactly? A spec/code divergence here is a blocker by our own rules.
5. The two plan docs' load-bearing claims (they cite `file:line` throughout).
   Plans are plans, not contracts — flag wrong claims, not disagreements with
   scope choices.

**Known risks / tradeoffs.**
- `cli/__init__.py` lazily imports `webui.app.serve` in the `ui` command — the
  one sanctioned coupling (mirrors `mcp serve`), judged intentional in an
  earlier security pass; ADR-0012 records it.
- Flash messages ride a query param (no sessions/cookies by design) — they are
  user-visible echo text, HTML-escaped by Jinja autoescape.
- G2/G3 are documented, deliberately not fixed here (scope discipline).
- `uv.lock` churn is mechanical (two new direct deps).
- The webui duplicates `_unified_diff`/`_fmt_metric` from the CLI rather than
  importing them (peer-layer rule); drift risk accepted and noted in code.

**How to verify.**
- `make ci` — full gate (ruff / format / mypy / pytest); 508 tests green at
  HEAD on this machine.
- Live: `uv run neurobase ui --root <tmp store> --port 8765` → browse
  `/suggestions`, accept a `target=project` proposal in a scratch store, check
  the diff preview, the backup dir, and the ledger event.
- CSRF: `curl -X POST http://127.0.0.1:8765/suggestions/x/reject` → 403.

**Out of scope.**
- Everything in the two forward plans (graph service, fold journal, app shell,
  gallery, status) — planned, not built.
- Fixing G2/G3.
- The visual re-skin (the current UI is deliberately plain; the app-shell plan
  supersedes its look).

---

## Reviewer findings  _(Reviewer — Codex)_

> Run the diff and review the actual code. One entry per finding.

**Verdict:** _pending_
