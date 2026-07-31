"""Suggestions review routes (Web UI Phase 1 plan, "Routes (Suggestions
only)"). Server-rendered Jinja2 pages over the same ``recommender`` modules
the CLI's ``recommend`` command group already uses — no new business logic,
just a second presentation layer (the plan's "Architecture" section).

Every GET handler here is side-effect-free (safe to call from a browser
without confirmation, safe to retry, safe to prefetch). Every mutating action
is a POST; the app-wide ``CSRFMiddleware`` (``webui/security.py``) already
rejects any POST that fails the same-origin/CSRF check before it ever reaches
a handler in this module, so handlers below don't re-check it themselves —
they only re-derive server-side state (``install.prepare_install`` is re-run
fresh inside the POST accept handler rather than trusting anything computed
during the earlier GET preview).
"""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from neurobase.core import projects, redact, search, store
from neurobase.core.config import load_config
from neurobase.core.store_handle import StoreHandle, StoreMode, open_store
from neurobase.recommender import corpus as recommend_corpus
from neurobase.recommender import install, proposals
from neurobase.recommender import metrics as recommend_metrics
from neurobase.webui import skills_scan

# Statuses `edit`/`accept` must never operate on — re-checked here so a stale link
# gives a clean 409 at the request boundary. The authoritative guard now lives
# inside the locked `proposals.save_edited_draft` (Codex P1-DATA-INTEGRITY-001 r2);
# this route-level check is the friendly early message, single-sourced from there.
_EDIT_BLOCKED_STATUSES = proposals.EDIT_BLOCKED_STATUSES


def suggestions_routes() -> list[Route]:
    """The full Suggestions route table (plan's "Routes" section)."""
    return [
        Route("/", _redirect_to_suggestions, methods=["GET"]),
        Route("/suggestions", _list_suggestions, methods=["GET"]),
        Route("/suggestions/{slug}", _suggestion_detail, methods=["GET"]),
        Route("/suggestions/{slug}/accept", _accept_view, methods=["GET", "POST"]),
        Route("/suggestions/{slug}/reject", _reject_view, methods=["POST"]),
        Route("/suggestions/{slug}/reopen", _reopen_view, methods=["POST"]),
        Route("/suggestions/{slug}/edit", _edit_view, methods=["GET", "POST"]),
    ]


def sessions_routes() -> list[Route]:
    """The Sessions surface (app-shell plan, Phase 2b): a read-only browser over
    the raw captures the scribes write, before the curator folds them."""
    return [
        Route("/sessions", _list_sessions, methods=["GET"]),
        Route("/sessions/{project}/{file}", _session_detail, methods=["GET"]),
    ]


def memory_routes() -> list[Route]:
    """The Memory + Search surface (app-shell plan, Phase 2c): a read-only browser
    over curated facts + synthesized nodes, plus keyword search."""
    return [
        Route("/memory", _list_memory, methods=["GET"]),
        Route("/memory/{project}/{slug}", _memory_detail, methods=["GET"]),
        Route("/search", _search, methods=["GET"]),
    ]


def skills_routes() -> list[Route]:
    """The Skills gallery (app-shell plan, Phase S): accepted proposals shown as
    installed skills/rules, with the drift-repair revert action (ADR-0020)."""
    return [
        Route("/skills", _skills_gallery, methods=["GET"]),
        Route("/skills/{slug}/revert", _revert_view, methods=["POST"]),
    ]


def projects_routes() -> list[Route]:
    """The Projects directory (app-shell plan, Phase P): which folders have
    Neurobase enabled, and what state each one is actually in.

    **Read-only, deliberately** (ADR-0027). Registry *editing* from the browser was
    built and then deferred to its own branch: three review rounds found the same
    class of defect repeatedly — ``load_registry`` preserves arbitrary hand-edited
    content by contract, and every mutating route trusted it. Editing hostile
    registry state safely is its own problem and gets its own review, rather than
    riding along with the surface that merely displays it.

    Everything here goes through ``core`` (``projects.*`` / ``StoreHandle``) —
    nothing builds a store path or parses the registry itself (ADR-0015)."""
    return [
        Route("/projects", _list_projects, methods=["GET"]),
    ]


def all_routes() -> list[Route]:
    """Every surface's routes, in one table for the Starlette app (``app.py``)."""
    return [
        *suggestions_routes(),
        *sessions_routes(),
        *memory_routes(),
        *skills_routes(),
        *projects_routes(),
    ]


# --- small shared helpers ---------------------------------------------------


def _root(request: Request) -> Path:
    root = request.app.state.root
    assert isinstance(root, Path)
    return root


def _read_root(request: Request) -> Path:
    """Open a request-local READ ``StoreHandle`` — running the D11 schema guard at
    THIS request boundary, not just once when the server started — and return its
    validated root for the ``recommender``/``metrics`` calls below (whose
    signatures still take ``root: Path`` until the ADR-0015 migration reaches
    them). READ never creates ``store.toml``, so a GET against an absent store
    stays a pure no-op read. An unsupported or unreadable schema raises
    :class:`store.UnsupportedSchemaError`, which ``unsupported_schema_handler``
    turns into a fail-safe typed page (Codex P1-SAFETY-SECURITY-002, spec
    §10/D11)."""
    return open_store(_root(request), StoreMode.READ).root


def _write_root(request: Request) -> Path:
    """Like :func:`_read_root`, but a WRITE handle for a mutating POST: the schema
    guard runs at the request boundary *before* any proposal/ledger write, so a
    long-running server that started on a supported store still refuses to mutate
    forward-schema state after the on-disk store advances under it (Codex
    P1-SAFETY-SECURITY-002)."""
    return open_store(_root(request), StoreMode.WRITE).root


async def unsupported_schema_handler(request: Request, exc: Exception) -> Response:
    """App-wide fail-safe for a D11 schema-guard failure raised at *any* request
    boundary — the request-local Suggestions handles above, and the
    Sessions/Memory/Search/Skills handles that open a READ handle inline. Renders
    the typed error page (409) rather than leaking a raw 500/stack trace when the
    store on disk is newer than this binary supports or its metadata is unreadable
    (Codex P1-SAFETY-SECURITY-002, spec §10/D11). Registered in ``app.build_app``."""
    return _error_response(request, 409, str(exc))


def _templates(request: Request) -> Jinja2Templates:
    templates = request.app.state.templates
    assert isinstance(templates, Jinja2Templates)
    return templates


def _base_context(request: Request) -> dict[str, Any]:
    """Context every template that renders a mutating form needs: the
    per-process CSRF token (the field *name* is a Jinja global registered on
    the environment in ``app.py``, not re-passed per route)."""
    return {"csrf_token": request.app.state.csrf_token}


def _clean_str(value: object) -> str | None:
    """A non-empty ``str``, or ``None`` — collapses both "absent" and
    "submitted blank" to the same "not provided" signal, and narrows a form
    field's ``str | UploadFile`` type down for mypy."""
    return value if isinstance(value, str) and value else None


def _redirect_to(path: str, message: str) -> RedirectResponse:
    """303 (See Other) to ``path`` after a POST — the correct status for a
    post/redirect/get so a page reload never re-submits the form — with a short
    human-readable flash message carried as a query param (this app has no
    session/cookie state to stash it in, and doesn't need one for a single-user
    local tool).

    ``path`` is always a literal built by a handler in this module, never anything
    derived from a request: this must not become an open-redirect helper."""
    return RedirectResponse(f"{path}?{urlencode({'flash': message})}", status_code=303)


def _redirect_with_flash(slug: str, message: str) -> RedirectResponse:
    """:func:`_redirect_to` aimed at a proposal's detail page."""
    return _redirect_to(f"/suggestions/{slug}", message)


def _error_response(request: Request, status_code: int, message: str) -> Response:
    """A clear, in-template error page — never a raw 500/stack trace for an
    expected failure mode (not-found, decided-status conflict, malformed
    proposal)."""
    context = {"status_code": status_code, "message": message}
    return _templates(request).TemplateResponse(
        request, "error.html", context, status_code=status_code
    )


async def _redirect_to_suggestions(request: Request) -> RedirectResponse:
    return RedirectResponse("/suggestions")


# --- GET /suggestions --------------------------------------------------------


def _fmt_metric(value: float | None) -> str:
    """Mirrors the CLI's ``_fmt_metric`` (``cli/__init__.py``): ``None``
    prints as "insufficient data", never a crash/blank/zero."""
    return "insufficient data" if value is None else f"{value:.4f}"


def _metrics_context(result: recommend_metrics.Metrics) -> dict[str, Any]:
    survival = result.survival
    if not survival:
        # §12.9: zero ledger-confirmed accepted proposals is "no data", not a
        # measured zero (mirrors `_print_recommender_metrics`).
        survival_summary = "insufficient data"
    else:
        survived = sum(1 for v in survival.values() if v == "survived")
        not_survived = sum(1 for v in survival.values() if v == "not_survived")
        insufficient = sum(1 for v in survival.values() if v == "insufficient_data")
        survival_summary = (
            f"{survived} survived, {not_survived} not survived, {insufficient} insufficient data"
        )
    return {
        "decided": result.decided,
        "accepted": result.accepted,
        "rejected": result.rejected,
        "precision": _fmt_metric(result.precision),
        "edited_rate": _fmt_metric(result.edited_rate),
        "reviewed_events": result.reviewed_events,
        "survival_summary": survival_summary,
        "recurrence_reduction": _fmt_metric(result.recurrence_reduction),
    }


def _list_row(doc: store.Document) -> dict[str, Any]:
    scores = doc.get("scores") if isinstance(doc.get("scores"), dict) else {}
    return {
        "slug": doc.get("name") or doc.file_path.stem,
        "status": str(doc.get("status") or ""),
        "type": doc.get("type"),
        "target": doc.get("target"),
        "project": doc.get("project"),
        "total": scores.get("total", 0),
        "created_at": doc.get("created_at"),
    }


# The queue filter chips: label → the statuses each shows. "pending" is the
# default so the queue is the *review* queue (decided items are one click away),
# matching the rail badge which also counts only pending.
_STATUS_FILTERS: dict[str, frozenset[str]] = {
    "pending": frozenset({"proposed"}),
    "accepted": frozenset({"accepted"}),
    "rejected": frozenset({"rejected"}),
    "all": frozenset({"proposed", "accepted", "rejected", "superseded"}),
}


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        key: sum(1 for r in rows if str(r["status"]) in statuses)
        for key, statuses in _STATUS_FILTERS.items()
    }


def _suggestions_context(
    request: Request, sel: dict[str, Any] | None, root: Path | None = None
) -> dict[str, Any]:
    """Shared two-pane context: the (filtered) queue on the left, plus the
    selected proposal or the metrics overview on the right. ``root`` lets a caller
    that already opened a request-local READ handle (e.g. ``_suggestion_detail``)
    reuse its validated root, so one request opens one handle and reads one store
    snapshot rather than two (Codex round-2 non-blocking note)."""
    if root is None:
        root = _read_root(request)
    all_rows = [_list_row(doc) for doc in proposals.load_all_proposals(root)]
    status = request.query_params.get("status", "pending")
    if status not in _STATUS_FILTERS:
        status = "pending"
    rows = [r for r in all_rows if str(r["status"]) in _STATUS_FILTERS[status]]
    return {
        **_base_context(request),
        "rows": rows,
        "counts": _status_counts(all_rows),
        "status": status,
        "metrics": _metrics_context(recommend_metrics.compute_metrics(root)),
        "sel": sel,
    }


async def _list_suggestions(request: Request) -> Response:
    context = _suggestions_context(request, sel=None)
    return _templates(request).TemplateResponse(request, "suggestions_list.html", context)


# --- GET /suggestions/{slug} -------------------------------------------------


def _evidence_rows(root: Path, doc: store.Document) -> list[dict[str, Any]]:
    """One row per evidence item, resolved via ``corpus.resolve_evidence`` —
    mirrors the ``[resolved]``/``[unresolved]`` pattern `recommend show`
    prints (``cli/__init__.py:recommend_show``)."""
    rows: list[dict[str, Any]] = []
    for item in doc.get("evidence") or []:
        if not isinstance(item, dict):
            rows.append({"raw": repr(item), "status": "unresolved"})
            continue
        try:
            ref = recommend_corpus.EvidenceRef.from_frontmatter(item)
        except (KeyError, ValueError):
            rows.append({"raw": repr(item), "status": "unresolved"})
            continue
        resolved = recommend_corpus.resolve_evidence(root, ref)
        rows.append(
            {
                "kind": ref.kind,
                "project": ref.project,
                "slug": ref.slug,
                "file": ref.file,
                "status": resolved.status,
                "path": str(resolved.path) if resolved.path is not None else None,
                "tombstoned": resolved.tombstoned,
            }
        )
    return rows


async def _suggestion_detail(request: Request) -> Response:
    root = _read_root(request)
    slug = request.path_params["slug"]
    doc = proposals.load_proposal(root, slug)
    if doc is None:
        return _error_response(request, 404, f"proposal {slug!r} not found or malformed")

    scores = doc.get("scores") if isinstance(doc.get("scores"), dict) else {}
    sel: dict[str, Any] = {
        "slug": slug,
        "status": str(doc.get("status") or ""),
        "type": doc.get("type"),
        "target": doc.get("target"),
        "project": doc.get("project"),
        "candidate_type": doc.get("candidate_type"),
        "scores": scores,
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "supersedes": doc.get("supersedes") or [],
        "installed_path": doc.get("installed_path"),
        # §12.8/D15(b): redact again at display time, same as `recommend
        # show` — never render an unredacted draft, even a hand-edited or
        # legacy one written before a redaction pattern existed.
        "body": proposals.redact_body(doc.body).rstrip(),
        "evidence": _evidence_rows(root, doc),
        "history": proposals.ledger_history(root, slug),
        "flash": request.query_params.get("flash"),
    }
    # ?fragment=1 → just the right-pane HTML (swapped in without reload); the
    # reject form inside needs the CSRF token, so pass _base_context. ?view=full →
    # the standalone page; otherwise the proposal opens inline in the two-pane.
    if request.query_params.get("fragment"):
        ctx = {**_base_context(request), "sel": sel}
        return _templates(request).TemplateResponse(request, "_suggestion_pane.html", ctx)
    if request.query_params.get("view") == "full":
        ctx = {**_base_context(request), "sel": sel}
        return _templates(request).TemplateResponse(request, "suggestion_detail.html", ctx)
    return _templates(request).TemplateResponse(
        request, "suggestions_list.html", _suggestions_context(request, sel=sel, root=root)
    )


# --- GET/POST /suggestions/{slug}/accept ------------------------------------


def _unified_diff(before: str, after: str, path: Path) -> str:
    """Same shape as the CLI's private ``_unified_diff``
    (``cli/__init__.py``), duplicated rather than imported: ``webui`` and
    ``cli`` are peer presentation layers over the same core and must not
    import each other (the plan's "Architecture" section)."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path} (current)",
            tofile=f"{path} (proposed)",
        )
    )


def _diff_line_class(line: str) -> str:
    if line.startswith("+") and not line.startswith("+++"):
        return "diff-add"
    if line.startswith("-") and not line.startswith("---"):
        return "diff-del"
    return ""


def _preview_fingerprint(preview: install.InstallPreview) -> str:
    """A server-verifiable identity for one rendered preview: resolved path,
    target scope, and the exact before/after bytes. Carried as a hidden form
    field and re-checked inside the committing POST, so consent binds to the
    precise diff the user saw (§14) — a proposal, registry, or target-file
    change between preview and commit must force a re-preview, never install
    unpreviewed bytes.

    Components are length-prefixed before hashing (injective encoding):
    artifact text may legally contain any character, so no delimiter — NUL
    included — can be trusted to separate fields unambiguously."""
    artifact = preview.artifact
    digest = hashlib.sha256()
    for part in (
        str(artifact.path.resolve()),
        str(artifact.target),
        artifact.before,
        artifact.after,
    ):
        data = part.encode("utf-8")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _run_prepare_install(
    request: Request, root: Path, slug: str, target: str | None
) -> install.InstallPreview | Response:
    """Shared by the GET preview and the POST commit: run
    ``install.prepare_install`` and turn its typed exceptions into a clear
    error response instead of a stack trace. Returns the preview on success,
    or the ``Response`` to send back on failure."""
    try:
        return install.prepare_install(root, slug, target=target)
    except install.ProposalNotFoundError:
        return _error_response(request, 404, f"proposal {slug!r} not found or malformed")
    except install.ProposalDecidedError as exc:
        return _error_response(request, 409, str(exc))
    except ValueError as exc:
        return _error_response(request, 400, str(exc))


async def _accept_view(request: Request) -> Response:
    # GET previews (READ, never materializes store.toml); POST commits (WRITE, the
    # guard runs before install writes anything) — Codex P1-SAFETY-SECURITY-002.
    root = _read_root(request) if request.method == "GET" else _write_root(request)
    slug = request.path_params["slug"]

    if request.method == "GET":
        target = _clean_str(request.query_params.get("target"))
        outcome = _run_prepare_install(request, root, slug, target)
        if isinstance(outcome, Response):
            return outcome
        preview = outcome
        artifact = preview.artifact
        diff = _unified_diff(artifact.before, artifact.after, artifact.path)
        diff_lines = [(line.rstrip("\n"), _diff_line_class(line)) for line in diff.splitlines()]
        context: dict[str, Any] = {
            **_base_context(request),
            "slug": slug,
            "target_path": str(artifact.path),
            "artifact_target": artifact.target,
            "foreign": artifact.foreign,
            "already_up_to_date": preview.already_up_to_date,
            "diff_lines": diff_lines,
            "requested_target": target or "",
            "is_skill": str(preview.doc.get("type")) == "skill",
            "fingerprint": _preview_fingerprint(preview),
        }
        return _templates(request).TemplateResponse(request, "suggestion_accept.html", context)

    # POST: never trust the GET-time preview — re-run prepare_install fresh
    # inside the request that actually writes (plan's "Routes" section).
    form = await request.form()
    target = _clean_str(form.get("target"))
    outcome = _run_prepare_install(request, root, slug, target)
    if isinstance(outcome, Response):
        return outcome
    preview = outcome

    # §14: consent binds to the exact previewed diff. The fresh preparation
    # above prevents writing stale bytes; this check prevents the converse —
    # writing *fresh* bytes the user never previewed.
    submitted = form.get("fingerprint")
    if not isinstance(submitted, str) or submitted != _preview_fingerprint(preview):
        return _error_response(
            request,
            409,
            f"proposal {slug!r} (or its install target) changed after the diff "
            "was previewed — nothing was installed. Re-review the new diff.",
        )

    if preview.already_up_to_date:
        if not preview.records_acceptance:
            # Mirrors `recommend accept`'s own early return: the proposal is
            # already accepted, so nothing is written and its decision stands.
            return _redirect_with_flash(slug, "Already up to date — nothing to install.")
        # ADR-0020 D44: the artifact is installed but the proposal doesn't say
        # so (the state a drift-repair `revert` leaves behind). Nothing to
        # write; record the acceptance. This POST — CSRF-checked and bound to
        # the previewed fingerprint above — is the consent. The preview was
        # re-prepared fresh in this request, but `record_acceptance` re-reads the
        # target at the write boundary anyway (P1-DATA-INTEGRITY-001); surface a
        # 409 if it changed under us rather than record a stale hash.
        try:
            recorded = install.record_acceptance(root, preview)
        except install.StalePreviewError as exc:
            # Target or proposal moved under us between preparation and the write
            # (P1-DATA-INTEGRITY-001 / -004) — record nothing, surface a 409.
            return _error_response(request, 409, str(exc))
        return _redirect_with_flash(
            slug, f"Already installed at {recorded.path} — recorded the acceptance."
        )

    result = install.commit_install(root, preview)
    message = f"Accepted: installed to {result.path}."
    if result.backup_dir is not None:
        message += f" Backed up existing artifact to {result.backup_dir}."
    return _redirect_with_flash(slug, message)


# --- POST /suggestions/{slug}/reject ----------------------------------------


async def _reject_view(request: Request) -> Response:
    root = _write_root(request)
    slug = request.path_params["slug"]
    doc = proposals.load_proposal(root, slug)
    if doc is None:
        return _error_response(request, 404, f"proposal {slug!r} not found or malformed")

    form = await request.form()
    reason = _clean_str(form.get("reason"))
    try:
        proposals.reject_proposal(root, slug, reason=reason)
    except ValueError as exc:
        return _error_response(request, 409, str(exc))
    return _redirect_with_flash(slug, "Rejected.")


async def _reopen_view(request: Request) -> Response:
    """Reconsider a rejected proposal (ADR-0024). The app-wide CSRF/same-origin
    gate has already cleared this POST; ``reopen_proposal`` re-checks the status
    at write time and refuses anything but ``rejected`` with a named-status 409,
    so a stale "Reconsider" link fails closed rather than mis-transitioning."""
    root = _write_root(request)
    slug = request.path_params["slug"]
    doc = proposals.load_proposal(root, slug)
    if doc is None:
        return _error_response(request, 404, f"proposal {slug!r} not found or malformed")
    try:
        proposals.reopen_proposal(root, slug)
    except ValueError as exc:
        return _error_response(request, 409, str(exc))
    return _redirect_with_flash(slug, "Reopened — back in the review queue.")


# --- GET/POST /suggestions/{slug}/edit --------------------------------------


async def _edit_view(request: Request) -> Response:
    # GET renders the draft (READ); POST persists the edit (WRITE) — the schema
    # guard runs at this boundary either way (Codex P1-SAFETY-SECURITY-002).
    root = _read_root(request) if request.method == "GET" else _write_root(request)
    slug = request.path_params["slug"]
    doc = proposals.load_proposal(root, slug)
    if doc is None:
        return _error_response(request, 404, f"proposal {slug!r} not found or malformed")

    status = str(doc.get("status") or "proposed")
    if status in _EDIT_BLOCKED_STATUSES:
        return _error_response(request, 409, f"cannot edit proposal {slug!r}: status is {status}")

    if request.method == "GET":
        draft = proposals.extract_draft(doc.body)
        if draft is None:
            return _error_response(request, 400, f"proposal {slug!r} has no managed draft region")
        # §14/§12.8: redact at display time on EVERY draft surface — a legacy
        # or hand-edited proposal may carry secrets the write paths never saw.
        context = {**_base_context(request), "slug": slug, "draft": proposals.redact_body(draft)}
        return _templates(request).TemplateResponse(request, "suggestion_edit.html", context)

    form = await request.form()
    draft_raw = form.get("draft")
    draft = draft_raw if isinstance(draft_raw, str) else ""
    try:
        saved = proposals.save_edited_draft(root, slug, draft)
    except proposals.EditBlockedError as exc:
        # The proposal was decided (e.g. rejected) between this handler's earlier
        # status check and the locked write — a decided-status conflict is a 409,
        # not the 400 reserved for malformed input (§14, Codex P2-UX-API-CONTRACT-010).
        return _error_response(request, 409, str(exc))
    if not saved:
        return _error_response(request, 400, "could not save edited draft")
    return _redirect_with_flash(slug, "Draft updated.")


# --- Sessions (Phase 2b) ----------------------------------------------------


def _redact_display(text: str) -> str:
    """Redact at display time on every raw surface — a raw is scrubbed at capture
    (D13), but a legacy/hand-edited capture may carry secrets the scribe never
    saw, so re-scrub with the same pass every write path uses (§14 defense)."""
    return redact.redact(text, load_config().redact.extra_patterns)


def _within_store(path: Path, store_root: Path) -> bool:
    """True when ``path`` resolves to somewhere at or beneath the validated store
    root — the same containment ``StoreHandle._require_within_store`` enforces for
    the handle's own writes (``..`` / absolute / **symlink**), applied to these
    direct filesystem reads. A symlinked ``raw/`` / ``curated/`` / ``nodes/``
    directory resolves *outside* the store, so files reached through it are
    refused even though they sit directly inside that resolved directory (Codex
    P2-SAFETY-SECURITY-003)."""
    root = store_root.resolve()
    resolved = path.resolve()
    return resolved == root or resolved.is_relative_to(root)


def _capture_path(raw_dir: Path, file: str, store_root: Path) -> Path | None:
    """Resolve a capture filename to a real file **directly inside** ``raw_dir``
    *and* beneath ``store_root``, or ``None``. Defense-in-depth: ``{file}`` is a
    single URL segment (Starlette won't match a ``/``), but a crafted ``..``-bearing
    name is refused here rather than trusted — the resolved path must have
    ``raw_dir`` as its immediate parent, so it can neither escape the project's
    ``raw/`` nor dip into a subdirectory. The extra ``store_root`` containment
    catches the case where ``raw/`` *itself* is a symlink out of the store: the
    file is then legitimately inside the resolved ``raw_dir`` but outside the
    validated store, and must not be read (Codex P2-SAFETY-SECURITY-003).
    ``raw_dir`` must already be ``.resolve()``-d by the caller."""
    if not _within_store(raw_dir, store_root):
        return None
    path = (raw_dir / file).resolve()
    if path.parent != raw_dir or not path.is_file():
        return None
    if not _within_store(path, store_root):
        return None
    return path


def _session_row(project: str, doc: store.Document) -> dict[str, Any]:
    session_id = str(doc.get("session_id") or "")
    return {
        "project": project,
        "file": doc.file_path.name,
        "session": session_id[:8] or "—",
        "agent": str(doc.get("agent") or "—"),
        "branch": str(doc.get("branch") or "—"),
        "captured_at": doc.get("captured_at"),
        "consumed": bool(doc.get("consumed")),
    }


def _session_rows(handle: Any) -> list[dict[str, Any]]:
    """Every raw capture across projects, newest-first. Fail-soft per project so
    one unreadable project can't blank the whole surface."""
    rows: list[dict[str, Any]] = []
    for project in handle.load_registry():
        try:
            for doc in handle.list_raw(project, unconsumed_only=False):
                # Skip a capture that resolves outside the store — a symlinked
                # `raw/` dir (or entry) must not surface external metadata on the
                # list, just as the detail read refuses it (Codex P2-SAFETY-SECURITY-003).
                if not handle.contains(doc.file_path):
                    continue
                rows.append(_session_row(project, doc))
        except (OSError, ValueError):
            continue
    rows.sort(key=lambda r: str(r["captured_at"] or ""), reverse=True)
    return rows


async def _list_sessions(request: Request) -> Response:
    handle = open_store(_root(request), StoreMode.READ)
    rows = _session_rows(handle)
    unconsumed = sum(1 for r in rows if not r["consumed"])
    context = {"rows": rows, "unconsumed": unconsumed, "sel": None}
    return _templates(request).TemplateResponse(request, "sessions_list.html", context)


async def _session_detail(request: Request) -> Response:
    root = _root(request)
    project = request.path_params["project"]
    file = request.path_params["file"]
    handle = open_store(root, StoreMode.READ)
    try:
        raw_dir = (handle.memory_dir(project) / "raw").resolve()
    except store.InvalidSlugError:
        return _error_response(request, 404, "Unknown project.")
    path = _capture_path(raw_dir, file, handle.root)
    if path is None:
        return _error_response(request, 404, "No such capture.")
    try:
        doc = store.read_doc(path)
    except (ValueError, OSError):
        return _error_response(request, 404, "This capture is unreadable.")
    session_id = str(doc.get("session_id") or "")
    sel = {
        "project": project,
        "file": file,
        "session": session_id[:8] or "—",
        "session_full": session_id,
        "agent": str(doc.get("agent") or "—"),
        "branch": str(doc.get("branch") or "—"),
        "cwd": str(doc.get("cwd") or "—"),
        "captured_at": doc.get("captured_at"),
        "consumed": bool(doc.get("consumed")),
        "body": _redact_display(doc.body),
    }
    # ?fragment=1 → just the right-pane HTML, fetched by the list's inline JS so a
    # click swaps the pane without a full-page reload (no flicker). ?view=full →
    # the standalone page (the "Open as page" link). Otherwise the full two-pane.
    if request.query_params.get("fragment"):
        return _templates(request).TemplateResponse(request, "_session_pane.html", {"sel": sel})
    if request.query_params.get("view") == "full":
        return _templates(request).TemplateResponse(request, "session_detail.html", {"sel": sel})
    rows = _session_rows(handle)
    unconsumed = sum(1 for r in rows if not r["consumed"])
    context = {"rows": rows, "unconsumed": unconsumed, "sel": sel}
    return _templates(request).TemplateResponse(request, "sessions_list.html", context)


# --- Memory + Search (Phase 2c) ---------------------------------------------


def _fact_row(project: str, doc: store.Document) -> dict[str, Any]:
    provenance = doc.get("provenance") if isinstance(doc.get("provenance"), list) else []
    supersedes = doc.get("supersedes") if isinstance(doc.get("supersedes"), list) else []
    return {
        "project": project,
        "slug": str(doc.get("name") or doc.file_path.stem),
        "updated_at": doc.get("updated_at"),
        "provenance": len(provenance),
        "supersedes": len(supersedes),
        "agent_last": str(doc.get("agent_last") or "—"),
    }


def _read_nodes(handle: Any, project: str) -> list[dict[str, Any]]:
    """The project's synthesized node(s) — the curator's regenerated summary of
    active memory. Fail-soft: an unreadable node is skipped, never fatal."""
    nodes_dir = handle.memory_dir(project) / "nodes"
    out: list[dict[str, Any]] = []
    if not nodes_dir.is_dir():
        return out
    for path in sorted(nodes_dir.glob("*.md")):
        # Skip anything that resolves outside the store — e.g. a symlinked `nodes/`
        # dir, or a symlinked entry within it (Codex P2-SAFETY-SECURITY-003).
        if not _within_store(path, handle.root):
            continue
        try:
            doc = store.read_doc(path)
        except (ValueError, OSError):
            continue
        out.append(
            {
                "name": str(doc.get("name") or path.stem),
                "generated_at": doc.get("generated_at"),
                "body": _redact_display(doc.body),
            }
        )
    return out


def _memory_facts(handle: Any) -> list[dict[str, Any]]:
    """Every active curated fact across projects, newest-first. Fail-soft per
    project so one unreadable project can't blank the whole surface."""
    facts: list[dict[str, Any]] = []
    for project in sorted(handle.load_registry()):
        try:
            facts.extend(
                _fact_row(project, d)
                for d in handle.list_curated(project, True)
                # A symlinked `curated/` dir (or entry) resolves outside the store;
                # don't list an external fact (Codex P2-SAFETY-SECURITY-003).
                if handle.contains(d.file_path)
            )
        except (OSError, ValueError):
            continue
    facts.sort(key=lambda f: str(f["updated_at"] or ""), reverse=True)
    return facts


def _all_nodes(handle: Any) -> list[dict[str, Any]]:
    """Every project's synthesized node — the default right-pane overview until a
    fact is selected."""
    nodes: list[dict[str, Any]] = []
    for project in sorted(handle.load_registry()):
        nodes.extend({**n, "project": project} for n in _read_nodes(handle, project))
    return nodes


async def _list_memory(request: Request) -> Response:
    handle = open_store(_root(request), StoreMode.READ)
    context = {"facts": _memory_facts(handle), "nodes": _all_nodes(handle), "sel": None}
    return _templates(request).TemplateResponse(request, "memory_list.html", context)


async def _memory_detail(request: Request) -> Response:
    root = _root(request)
    project = request.path_params["project"]
    slug = request.path_params["slug"]
    handle = open_store(root, StoreMode.READ)
    try:
        curated_dir = handle.memory_dir(project) / "curated"
    except store.InvalidSlugError:
        return _error_response(request, 404, "Unknown project.")
    # A curated slug is `^[a-z0-9-]+$` (store.SLUG_RE) — validating it both rejects
    # a bad request and structurally forecloses traversal (no `.`/`/` can appear).
    if not store.SLUG_RE.match(slug):
        return _error_response(request, 404, "No such fact.")
    path = curated_dir / f"{slug}.md"
    # A valid slug can't escape `curated/`, but `curated/` itself could be a symlink
    # out of the store — require the resolved file beneath the validated root before
    # reading (Codex P2-SAFETY-SECURITY-003).
    if not _within_store(path, handle.root) or not path.is_file():
        return _error_response(request, 404, "No such fact.")
    try:
        doc = store.read_doc(path)
    except (ValueError, OSError):
        return _error_response(request, 404, "This fact is unreadable.")
    provenance = doc.get("provenance") if isinstance(doc.get("provenance"), list) else []
    supersedes = doc.get("supersedes") if isinstance(doc.get("supersedes"), list) else []
    sel = {
        "project": project,
        "slug": str(doc.get("name") or slug),
        "status": str(doc.get("status") or "active"),
        "agent_last": str(doc.get("agent_last") or "—"),
        "updated_at": doc.get("updated_at"),
        "provenance": [_prov_link(project, p) for p in provenance],
        "supersedes": [str(s) for s in supersedes],
        "body": _redact_display(doc.body),
    }
    # ?fragment=1 → just the right-pane HTML (swapped in by the list's inline JS);
    # ?view=full → the standalone page; otherwise the fact opens inline in the
    # Memory two-pane, facts list still on the left.
    if request.query_params.get("fragment"):
        return _templates(request).TemplateResponse(request, "_memory_pane.html", {"sel": sel})
    if request.query_params.get("view") == "full":
        return _templates(request).TemplateResponse(request, "memory_detail.html", {"sel": sel})
    context = {"facts": _memory_facts(handle), "nodes": _all_nodes(handle), "sel": sel}
    return _templates(request).TemplateResponse(request, "memory_list.html", context)


def _prov_link(project: str, entry: object) -> dict[str, Any]:
    """Shape one provenance entry for display, linking it back to its source
    capture when it names a raw (``raw/<file>``) — the Memory→Sessions half of
    the provenance chain. Anything else renders as plain text."""
    label = str(entry)
    href = f"/sessions/{project}/{label[len('raw/') :]}" if label.startswith("raw/") else None
    return {"label": label, "href": href}


def _hit_row(hit: search.SearchHit) -> dict[str, Any]:
    href = f"/memory/{hit.project}/{hit.name}" if hit.kind == "curated" else "/memory"
    return {
        "project": hit.project,
        "name": hit.name,
        "kind": hit.kind,
        "score": hit.score,
        "snippet": _redact_display(hit.snippet),
        "href": href,
    }


async def _search(request: Request) -> Response:
    root = _root(request)
    query = (request.query_params.get("q") or "").strip()
    hits: list[dict[str, Any]] = []
    if query:
        handle = open_store(root, StoreMode.READ)
        hits = [_hit_row(h) for h in search.search(handle, query)]
    context = {"q": query, "hits": hits}
    return _templates(request).TemplateResponse(request, "search_results.html", context)


# --- Skills gallery (every skill on the machine) + revert -------------------

# artifact_state → the status-chip class the design system already ships.
_STATE_CHIP = {
    proposals.ARTIFACT_LIVE: "accepted",
    proposals.ARTIFACT_MISSING: "superseded",
    proposals.ARTIFACT_ORPHANED: "proposed",
    proposals.ARTIFACT_UNRESOLVABLE: "rejected",
}


def _discovered_card(skill: skills_scan.InstalledSkill) -> dict[str, Any]:
    """A SKILL.md found on disk — Neurobase-managed (carries our frontmatter) or
    external (hand-authored / from elsewhere). It's present, so nothing to revert.

    The name/description/path are read verbatim off an arbitrary third-party file
    on this machine and rendered, so they go through the same display-redaction
    pass every other surface uses — a secret-shaped external skill description must
    not render verbatim (Codex P2-SAFETY-SECURITY-006). ``nb_slug`` is an internal
    routing/ownership identifier (a validated canonical slug, never secret-shaped)
    and stays unredacted so links and ``present_nb`` keep working."""
    return {
        "name": _redact_display(skill.name),
        "description": _redact_display(skill.description),
        "scope": skill.scope,
        "project": skill.project,
        "path": _redact_display(skill.path),
        "source": "neurobase" if skill.managed else "external",
        "nb_slug": skill.nb_slug,
        "state": "installed",
        "chip": "accepted" if skill.managed else "superseded",
        "revertable": False,
    }


def _missing_skill_card(root: Path, doc: store.Document) -> dict[str, Any]:
    """A Neurobase skill proposal accepted but whose SKILL.md wasn't found on this
    machine — drift. Kept visible and revertable (ADR-0020 liveness guard)."""
    slug = str(doc.get("name") or doc.file_path.stem)
    state = proposals.artifact_state(root, doc, slug)
    return {
        "name": slug,
        "description": str(doc.get("candidate_type") or ""),
        "scope": "user" if doc.get("target") == "user-skill" else "project",
        "project": doc.get("project"),
        "path": doc.get("installed_path") or "—",
        "source": "neurobase",
        "nb_slug": slug,
        "state": state,
        "chip": _STATE_CHIP.get(state, "superseded"),
        "revertable": proposals.revert_refusal(slug, doc, state) is None,
    }


async def _skills_gallery(request: Request) -> Response:
    root = _root(request)
    handle = open_store(root, StoreMode.READ)
    discovered = skills_scan.discover_skills(handle)
    present_nb = {s.nb_slug for s in discovered if s.nb_slug}
    cards = [_discovered_card(s) for s in discovered]
    # Neurobase skill proposals accepted but not found on disk here → show them as
    # drift so they stay visible and revertable (artifact gone, or on another Mac).
    for doc in proposals.load_all_proposals(root):
        if str(doc.get("status")) != "accepted" or str(doc.get("type")) != "skill":
            continue
        if str(doc.get("name") or doc.file_path.stem) in present_nb:
            continue
        cards.append(_missing_skill_card(root, doc))
    counts = {
        "total": len(cards),
        "neurobase": sum(1 for c in cards if c["source"] == "neurobase"),
        "external": sum(1 for c in cards if c["source"] == "external"),
    }
    # _base_context supplies the CSRF token the revert form needs.
    context = {**_base_context(request), "cards": cards, "counts": counts}
    return _templates(request).TemplateResponse(request, "skills_list.html", context)


async def _revert_view(request: Request) -> Response:
    """Drift-repair revert (§12.7, G2, ADR-0020). The app-wide CSRF/same-origin
    gate has already cleared this POST; ``revert_proposal`` re-checks liveness at
    write time and refuses a still-live artifact, so a stale "Revert" link (the
    artifact came back, or another tab already reverted) fails closed with 409
    rather than orphaning it."""
    root = _write_root(request)
    slug = request.path_params["slug"]
    doc = proposals.load_proposal(root, slug)
    if doc is None:
        return _error_response(request, 404, f"proposal {slug!r} not found or malformed")
    try:
        proposals.revert_proposal(root, slug)
    except ValueError as exc:
        return _error_response(request, 409, str(exc))
    return _redirect_with_flash(slug, "Reverted to proposed — drift repaired.")


# --- Projects directory (app-shell plan, Phase P) ----------------------------
#
# Registry paths are rendered **verbatim**, without the display-redaction pass the
# draft/skill surfaces use. They are not third-party text: every one is a directory
# on this machine that the user typed into this page's own form (or into
# `neurobase enable`). Redacting them would defeat the surface — a root has to be
# read to be recognized to be acted on at all, and the CLI takes the stored string
# verbatim, so a masked path could not be matched against the registry.
#
# The governing fact for everything below: `load_registry` preserves **arbitrary**
# hand-edited content by contract — any TOML table key, any string in `roots`. So
# nothing here may assume a slug is a slug or a root is a usable path.


def _project_root_row(path_str: str, denylist: list[str], auto_roots: list[str]) -> dict[str, Any]:
    """One registered root, with the three facts that decide whether it is actually
    doing anything: does the directory still exist, is it denylist-suppressed, and
    is it covered by folder-scoped auto-enable.

    A registered root is an arbitrary string from ``registry.toml``, so it may not
    be a usable path at all — an embedded NUL is the sharp case: ``Path.is_dir()``
    swallows it, but ``Path.resolve()`` inside the rule lookups raises ``ValueError``,
    which took down the whole page for one hand-edited entry (Codex F6). A root that
    cannot be turned into a path is therefore its own rendered state, and the two
    rule lookups are skipped for it rather than attempted and caught downstream."""
    try:
        path: Path | None = Path(path_str)
        usable = bool(path_str) and "\x00" not in path_str
    except (ValueError, TypeError):
        path, usable = None, False
    if path is None or not usable:
        return {
            "path": path_str,
            "usable": False,
            "exists": False,
            "denylisted": None,
            "auto_enabled": None,
        }
    return {
        "path": path_str,
        "usable": True,
        # `is_dir()` answers False (never raises) for an unreadable or malformed
        # path, which is the right reading here — "not a usable directory".
        "exists": path.is_dir(),
        "denylisted": projects.denylist_hit(path, denylist),
        "auto_enabled": projects.auto_enable_hit(path, auto_roots),
    }


def _project_row(
    handle: StoreHandle, slug: str, roots: list[str], denylist: list[str], auto_roots: list[str]
) -> dict[str, Any]:
    """One directory row. **Every** store read here is per-project fail-soft (§14):
    a single bad project degrades its own row, never the page — which is the only
    place to go and unregister it.

    The state that forces this is a registry entry whose *key* is not a valid slug.
    ``load_registry`` validates an entry's ``roots`` shape but deliberately never
    its key (it preserves hand-edited entries), so ``[projects."bad_slug"]`` in an
    otherwise valid ``registry.toml`` reaches here and every store accessor —
    ``list_raw``, ``list_curated``, ``node_count``, **and ``memory_dir``** — raises
    ``InvalidSlugError``. That last one is the trap: it is not a "count", so it does
    not look like it belongs inside the counting guard, and leaving it outside turned
    the whole directory into a 500 that hid every healthy project behind the one
    entry the user came to delete (Codex F1).

    So an invalid slug is a rendered **state**, not an exception. Such a project can
    have no tree and no contents by construction — no store path can be built for it
    — but `deregister` and `remove-root` touch only ``registry.toml`` and stay fully
    functional on it, which is what makes the row actionable rather than merely
    visible."""
    valid_slug = bool(store.SLUG_RE.match(slug))
    counts: dict[str, int] | None = None
    has_tree = False
    if valid_slug:
        try:
            raws = handle.list_raw(slug, unconsumed_only=False)
            counts = {
                "raw": len(raws),
                "unconsumed": sum(1 for doc in raws if not doc.get("consumed")),
                "curated": len(handle.list_curated(slug, active_only=True)),
                "nodes": handle.node_count(slug),
            }
            has_tree = handle.memory_dir(slug).exists()
        except (OSError, ValueError):
            counts = None
    root_rows = [_project_root_row(root, denylist, auto_roots) for root in roots]
    return {
        "slug": slug,
        "roots": root_rows,
        "registered": bool(roots),
        "counts": counts,
        "has_tree": has_tree,
        "valid_slug": valid_slug,
        # A project is suppressed only when *every* root it has is denylisted —
        # one live root is enough for capture to keep working.
        "suppressed": bool(root_rows) and all(r["denylisted"] for r in root_rows),
    }


def _projects_context(request: Request) -> dict[str, Any]:
    """The directory's full context: every registered project, plus every project
    that has a tree on disk with **no** registry entry.

    That second group is the point of comparing the two views. An unregistered tree
    is a dead end — nothing resolves to it, so no hook can capture into it and no
    sweep walks it — and until now nothing surfaced it at all.

    No CSRF token and no flash: this surface renders no form (ADR-0027)."""
    handle = open_store(_root(request), StoreMode.READ)
    config = load_config()
    denylist = config.enable.denylist
    auto_roots = config.enable.auto_enable_roots
    try:
        registry = handle.load_registry()
    except (OSError, ValueError):
        registry = {}
    rows = [
        _project_row(handle, slug, registry[slug], denylist, auto_roots)
        for slug in sorted(registry)
    ]
    orphans = [
        _project_row(handle, slug, [], denylist, auto_roots)
        for slug in handle.list_project_trees()
        if slug not in registry
    ]
    return {
        "projects": rows,
        "orphans": orphans,
        "store_root": str(handle.root),
        "denylist": denylist,
        "auto_enable_roots": auto_roots,
    }


async def _list_projects(request: Request) -> Response:
    """``GET /projects`` — the whole surface. Side-effect-free, like every other
    read route here."""
    return _templates(request).TemplateResponse(
        request, "projects_list.html", _projects_context(request)
    )


__all__ = [
    "all_routes",
    "memory_routes",
    "projects_routes",
    "sessions_routes",
    "skills_routes",
    "suggestions_routes",
    "unsupported_schema_handler",
]
