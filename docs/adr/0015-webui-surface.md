# ADR-0015: Web UI — loopback server as a peer presentation layer

- **Status:** Accepted
- **Date:** 2026-07-16
- **Resolves:** Web UI Phase 1 plan (`docs/notes/2026-07-15-webui-phase1-plan.md`)
- **Supersedes:** none

## Context

Every action already lives in the Python package and the CLI was the single
front door. A browser surface for reviewing recommender proposals (evidence,
scores, managed draft → accept/edit/reject) needs the same
diff → consent → backup → atomic-write → ledger discipline the CLI enforces,
without forking that logic or weakening the local-first/zero-cloud charter. The
accept choreography lived inline in `cli/recommend_accept`, unusable from a
second presentation layer split across two HTTP requests. And a local server
that can write `~/.claude/skills/` and project `AGENTS.md`/`CLAUDE.md` files is
a real write surface: localhost alone does not stop a drive-by POST from a
hostile web page.

## Decision

Ship `neurobase ui`: a server-rendered Starlette + Jinja2 app in a new
top-level `webui/` package that is a **peer of `cli/`** — both depend on
`core/`, `brain/`, and the mid tier; neither imports the other's code (the `ui`
command's lazy launch import of `webui.app.serve` is the one sanctioned
coupling, mirroring `mcp serve`). The accept choreography is extracted to
`recommender/install.py` (`prepare_install`/`commit_install`) so CLI and web UI
install through one code path; the CLI wrapper preserves its messages and exit
codes with `tests/test_cli_recommend.py` as the unmodified oracle. Security
posture: bind hard-pinned to `127.0.0.1` (no `--host`), every POST gated before
routing by a same-origin check plus a per-process CSRF token compared with
`secrets.compare_digest`, reads side-effect-free, no cookies or sessions,
drafts redacted at display time. No build step, no npm — server-rendered HTML
(vanilla JS only if a later phase needs it); `jinja2` and `uvicorn` become
direct base dependencies. The behavioral contract is recorded as spec
appendix **§14**.

## Consequences

- A proposal can be reviewed and installed from the browser with the exact
  §12 consent discipline; UI and CLI cannot drift on install behavior.
- `webui/` becomes a place later phases extend (sessions/memory readers, graph,
  skills gallery, status) under the same §14 posture; new mutating routes
  inherit the CSRF/same-origin gate from the app-wide middleware.
- The committing POST re-runs `prepare_install` fresh, so a stale GET-time
  preview can never be written.
- Two shipped-code gaps surfaced by live use are tracked as known-gaps G2
  (accepted-state/disk drift; no revert path) and G3 (emitter can double
  frontmatter; `description` misuses `candidate_type`).
- Exposure beyond loopback is out of contract; if ever proposed it requires
  real authentication first (deploy-safety gate), not a CORS list.

## Alternatives considered

- **Native Swift app first** — forks the stack; every action already lives in
  Python. A thin WKWebView shell can wrap this same server later.
- **htmx / a JS framework / build step** — violates the no-npm, zero-build
  stance for marginal Phase 1 benefit; server-rendered pages suffice.
- **Reusing the CLI via subprocess from the UI** — loses typed errors and
  in-process reuse; the extraction into `recommender/install.py` is the
  `recall_common.py` named-shared-module pattern instead.
- **Cookie/session-based CSRF** — persistence and state for a single-user
  loopback tool; a per-process token invalidated on restart is strictly
  simpler and correct here.
