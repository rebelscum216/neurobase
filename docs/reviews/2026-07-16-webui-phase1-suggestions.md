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

> Round 1 ran via `codex exec` (workspace-write sandbox, review-only); findings
> relayed verbatim by the author from the vault drop
> (`~/vault/outputs/reviews/neurobase/webui-phase1-suggestions.r1.review.md`,
> which holds the full text incl. required fixes and verification
> expectations). Codex confirmed HEAD `1abcfbc`, ran the full CI gate (508
> green), and proved all three P1s with live TestClient probes.

> **Revision 1 (author, 2026-07-16, commits `453ca5c` + `af22bfe`):** all five
> findings addressed as follow-up commits; full gate green (519 tests, +11).
> Per-finding resolutions below; round 2 re-review requested.

### F1 / P1-SECURITY-001 — arbitrary Host defeats the browser-origin boundary
- **severity:** blocker
- **location:** `src/neurobase/webui/security.py:60`
- **issue:** the middleware trusts the untrusted `Host` header as the
  authority; any Origin whose netloc equals it passes. Proven: `Host:
  evil.example:8765` + matching Origin + correct token reached routing. Under
  DNS rebinding a hostile page becomes same-origin with the loopback server
  and can drive every mutating route.
- **suggested direction:** reject non-loopback Host authorities (allowlist)
  before the origin comparison; extend §14/ADR-0012 so the allowlist is
  contract, not accident; test that a matching-but-foreign Host+Origin 403s.
- **resolution:** resolved — `is_loopback_host` allowlist
  (`127.0.0.1`/`localhost`/`::1`) enforced by the middleware on **every
  method** (not just POST, so a rebound page cannot read pages or the token
  either), re-checked inside `check_same_origin_csrf` for direct callers;
  §14 + ADR-0012 extended; tests cover the exact rebinding shape
  (matching-but-foreign Host+Origin+token → 403), localhost acceptance, and
  the hostname vocabulary.

### F2 / P1-CORRECTNESS-002 — commit POST can install bytes never previewed
- **severity:** blocker
- **location:** `src/neurobase/webui/routes.py:293`
- **issue:** the confirm form carries no fingerprint of the previewed result;
  the POST re-prepares fresh and commits whatever it gets. Proven: previewed
  `VERSION ONE`, edited the proposal, submitted the old form → installed
  `VERSION TWO`. Violates the §12/§14 exact-diff consent MUST.
- **suggested direction:** carry a server-verifiable fingerprint (resolved
  path/target + before/after bytes) in the form; on mismatch answer a typed
  409 re-preview with no backup/write/ledger. Keep the fresh POST prepare.
- **resolution:** resolved — sha256 over path\0target\0before\0after rendered
  as a hidden field; the POST re-prepares fresh (kept) and refuses with a
  typed 409 and zero side effects on missing/mismatched fingerprint; §14 gains
  the consent-binding MUST. Tests: the exact probe scenario (preview A, edit,
  submit stale form → 409, nothing written, fresh preview then commits) and
  the missing-fingerprint case.

### F3 / P1-SECURITY-003 — edit GET renders legacy drafts unredacted
- **severity:** blocker
- **location:** `src/neurobase/webui/routes.py:348`
- **issue:** the edit GET passes `extract_draft(doc.body)` straight into the
  textarea. Proven: a legacy proposal containing an `sk-...` secret rendered
  it literally. §14 requires display-time redaction on every draft surface.
- **suggested direction:** redact the extracted draft (shared configured
  redaction path) before templating; test with built-in + configured-extra
  patterns.
- **resolution:** resolved — `proposals.redact_body` applied to the extracted
  draft on the edit GET (same call as the detail surface); test injects a
  GitHub-token secret directly into the on-disk draft region and asserts the
  marker renders, not the secret. (Configured-extra pattern coverage exists in
  the redact/proposals suites; the route test proves the display path.)

### F4 / P2-TEST-GAP-004 — §14 MUSTs not fully covered by contract tests
- **severity:** major
- **location:** `tests/test_webui_app.py:34`
- **issue:** no tests for the loopback bind, pre-serve schema refusal, web
  no-op (no backup/no ledger), preview drift, edit-display redaction, or
  loopback-only Host acceptance; the same-origin success test normalizes the
  F1 weakness via TestClient's `testserver` authority.
- **suggested direction:** focused contract tests that fail at `1abcfbc` and
  pass after the fixes; full gate stays green.
- **resolution:** resolved — added: serve() binds 127.0.0.1 (monkeypatched
  uvicorn), `ui` refuses a newer-schema store before serve, web no-op POST
  (no new backup, no second accepted event), preview drift, edit-display
  redaction, loopback-Host acceptance + rejection; both TestClient fixtures
  pinned to `http://127.0.0.1:8765`. Gate: 519 passed.

### F5 / P3-DOCS-005 — provenance plan overstates the curator log as counts-only
- **severity:** nit
- **location:** `docs/notes/2026-07-16-provenance-plan.md:39`
- **issue:** the log records pass summaries (status/timestamp/optional error),
  not literally "only counts"; the real gap is no raw→fact identities/edges.
- **suggested direction:** reword; Slice B's compatibility reasoning should
  start from the exact record shape.
- **resolution:** resolved — reworded to "pass summaries — status, integer
  counts, timestamp, optional error, differently-shaped noop/resynth records —
  but no per-pass raw→fact identities or edges."

**Verdict (round 1):** changes-requested (Codex: BLOCKED) — _three P1s:
Host-boundary bypass, unpreviewed-bytes install, unredacted edit surface._
All findings addressed in revision 1; awaiting round 2.
