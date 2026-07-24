# Web UI Phase 1 — Suggestions review: execution plan

_2026-07-15 — working plan for the first phase of a local web UI. This is a plan,
not a contract. Not a build-plan phase (0–9 already shipped as v0.1.0) — this is
new, additive scope: a UI on top of the shipped CLI/store/recommender, kept to the
project's scope-discipline norm (tightly scoped, no unrelated refactoring)._

## Goal

A local, loopback-only web server (`neurobase ui`) that lets a human review
recommender proposals — evidence, scores, the managed draft — and **accept /
edit / reject** them through the browser, using the exact same diff → consent →
backup → atomic-write → ledger discipline the CLI already enforces. Read
surfaces for Sessions and Memory, and a Status/control panel, are later phases;
this phase is scoped to Suggestions only.

**Done when:** a proposal installs a real `SKILL.md` (or rule block) from the
browser, with a diff shown before commit and a backup written under
`<root>/backups/<ts>/` — end to end, against a real store.

## Why a web UI and not a native app first

Every action already lives in the Python package; the CLI is the single front
door (`docs/architecture.md`). A local web server is a *second presentation
layer* over the same core — in-process calls to `core`, `recommender`,
`emitters`, no subprocess bridge — and stays cross-platform, matching the
project's zero-cloud/zero-telemetry, single-language ethos. A native shell
(Swift/WKWebView) can wrap this server later without forking the stack.

## Architecture

```
neurobase ui  (new CLI command, lazily imports webui — same pattern as `mcp serve`)
  → uvicorn.run(app, host="127.0.0.1", port=8765)
      → src/neurobase/webui/app.py   Starlette app + same-origin/CSRF middleware
      → src/neurobase/webui/routes.py
          → src/neurobase/recommender/install.py   (NEW — shared with cli/)
          → src/neurobase/recommender/{proposals,corpus,metrics}.py  (unchanged, reused)
```

`webui/` is a new top-level package, a peer of `cli/` — both depend on `core/`,
`brain/`, and the mid tier; neither imports the other (the layer contract's "no
lateral import between edges" rule, same reasoning as `adapters/recall_common.py`).

## D-1 — Extract the install service (prerequisite, before any UI code)

Today the accept flow's diff → consent → backup → write → ledger choreography
lives entirely inside `cli/__init__.py:recommend_accept` (lines ~960–1012). The
web UI needs the same choreography but split across two HTTP requests (a GET
that previews the diff, a POST that commits it) instead of one blocking
`typer.confirm()` call. Lift the logic into `src/neurobase/recommender/install.py`:

```python
@dataclass(frozen=True)
class InstallPreview:
    doc: store.Document
    artifact: emitters.Artifact
    already_up_to_date: bool

class ProposalNotFoundError(LookupError): ...
class ProposalDecidedError(RuntimeError):
    def __init__(self, slug: str, status: str): ...

def prepare_install(root: Path, slug: str, *, target: str | None = None) -> InstallPreview:
    """Load + status-guard + render the artifact. No I/O writes. Raises
    ProposalNotFoundError / ProposalDecidedError / ValueError (malformed
    proposal / emitter failure) — never writes anything on any path."""

@dataclass(frozen=True)
class InstallResult:
    path: Path
    backup_dir: Path | None
    installed_hash: str

def commit_install(root: Path, preview: InstallPreview) -> InstallResult:
    """Backup → atomic write → accept_proposal(ledger). Caller (cli or webui)
    is responsible for having obtained consent before calling this."""
```

`cli/__init__.py:recommend_accept` becomes a thin wrapper calling
`prepare_install` then `commit_install`, **preserving every existing message,
exit code, and the no-op/foreign-warning short-circuits byte for byte** — this
is a refactor, not a behavior change, and `tests/test_cli_recommend.py` is the
oracle: it must pass unmodified.

`reject`/`edit` need no new service — `proposals.reject_proposal` /
`save_edited_draft` are already root+slug-in, no CLI-specific glue to extract.

## Routes (Suggestions only)

| Method | Path | Behavior |
|---|---|---|
| GET | `/` | redirect → `/suggestions` |
| GET | `/suggestions` | list (reuses `proposals.load_all_proposals`) + metrics strip (`metrics.compute_metrics`) |
| GET | `/suggestions/{slug}` | detail: draft, evidence (resolved via `recommender.corpus.resolve_evidence`), scores, history |
| GET | `/suggestions/{slug}/accept` | preview: calls `install.prepare_install`, renders the unified diff + a CSRF-protected confirm form (query param `?target=user\|project` for skills) |
| POST | `/suggestions/{slug}/accept` | validate CSRF + re-run `prepare_install` (never trust a stale GET-time diff) → `install.commit_install` → redirect to detail with a flash |
| POST | `/suggestions/{slug}/reject` | CSRF-protected; calls `proposals.reject_proposal`; optional `reason` form field |
| GET/POST | `/suggestions/{slug}/edit` | GET renders the draft in a textarea; POST (CSRF-protected) calls `proposals.save_edited_draft` |

Every read route is a GET with no side effects; every mutation is a POST
gated by the CSRF token, mirroring the CLI's `--yes` gate with a real "are you
sure" step instead of a flag.

## Security (this is a local write surface — treat it like one)

- **Bind `127.0.0.1` only** — never `0.0.0.0`. No `--host` flag in phase 1.
- **Same-origin check**: reject any POST whose `Origin` (or `Referer` when
  `Origin` is absent) doesn't match the request's own `Host`.
- **Per-process CSRF token**: `secrets.token_urlsafe(32)` generated once at
  server start, embedded as a hidden field in every form, checked on every
  POST. Not persisted — a server restart invalidates all outstanding forms,
  which is the correct behavior for a single-user local tool.
- No new secrets, no new persistent credential storage, no change to what
  gets written to disk beyond what the CLI's `accept`/`reject`/`edit` already
  write through the same functions.

## New dependencies

`starlette` and `httpx` are already present (transitive via the pinned `mcp`
SDK / its test extras). **`jinja2` and `uvicorn` are new direct dependencies**
— add both to `pyproject.toml [project] dependencies` (this project doesn't use
optional-dependency extras; everything is a base dep). Both are added as
*direct* pins even where already transitively present, so `webui/` doesn't rely
on `mcp`'s dependency graph by accident.

`webui` is lazily imported inside the new `ui` CLI command body — exactly the
pattern `mcp_app`'s `mcp_serve()` already uses for the `mcp` SDK — so starlette/
jinja2/uvicorn stay off the hook fast path and off every other command's import
graph.

## Visual direction

Server-rendered HTML + a small amount of vanilla JS (no build step, no npm
dependency). Palette/type tokens carry over from the published design-plan
artifact: a teal accent (`#0C8C86` light / `#2FCFC0` dark) on a cool-neutral
ground, monospace for slugs/scores/evidence refs, semantic color reserved for
proposal status chips (proposed/accepted/rejected), not the accent.

## Testing

- `tests/test_recommender_install.py` (new) — `prepare_install`/`commit_install`
  unit tests: not-found, decided-status guard, no-op (before==after), foreign
  warning surfaced via the preview, successful commit writes + backs up +
  records the ledger hash.
- `tests/test_cli_recommend.py` — must pass **unmodified** (the refactor's
  behavior-preservation oracle).
- `tests/test_webui.py` (new) — Starlette `TestClient` (httpx-based, already a
  dependency): list/detail render; accept preview shows a diff; POST without a
  CSRF token is rejected; POST with a mismatched Origin is rejected; a full
  accept commits a real file + backup in a tmp store; reject flips status;
  edit round-trips a draft.

## Out of scope for this phase

Sessions/Memory readers, Status/control panel, live filesystem watching, the
native Swift shell — later phases per the design-plan artifact.
