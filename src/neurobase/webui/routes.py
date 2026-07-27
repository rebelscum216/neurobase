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

from neurobase.core import redact, search, store
from neurobase.core.config import load_config
from neurobase.core.store_handle import StoreMode, open_store
from neurobase.recommender import corpus as recommend_corpus
from neurobase.recommender import install, proposals
from neurobase.recommender import metrics as recommend_metrics

# Statuses `edit`/`accept` must never operate on (mirrors `recommend edit`'s
# and `install.prepare_install`'s own guards — re-checked here so a stale
# link never reaches those functions with a decided proposal for `edit`,
# which has no typed-exception guard of its own).
_EDIT_BLOCKED_STATUSES = frozenset({"rejected", "superseded"})


def suggestions_routes() -> list[Route]:
    """The full Suggestions route table (plan's "Routes" section)."""
    return [
        Route("/", _redirect_to_suggestions, methods=["GET"]),
        Route("/suggestions", _list_suggestions, methods=["GET"]),
        Route("/suggestions/{slug}", _suggestion_detail, methods=["GET"]),
        Route("/suggestions/{slug}/accept", _accept_view, methods=["GET", "POST"]),
        Route("/suggestions/{slug}/reject", _reject_view, methods=["POST"]),
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


def all_routes() -> list[Route]:
    """Every surface's routes, in one table for the Starlette app (``app.py``)."""
    return [*suggestions_routes(), *sessions_routes(), *memory_routes()]


# --- small shared helpers ---------------------------------------------------


def _root(request: Request) -> Path:
    root = request.app.state.root
    assert isinstance(root, Path)
    return root


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


def _redirect_with_flash(slug: str, message: str) -> RedirectResponse:
    """303 (See Other) back to the detail page after a POST — the correct
    status for a post/redirect/get so a page reload never re-submits the
    form — with a short human-readable flash message carried as a query
    param (this app has no session/cookie state to stash it in, and doesn't
    need one for a single-user local tool)."""
    query = urlencode({"flash": message})
    return RedirectResponse(f"/suggestions/{slug}?{query}", status_code=303)


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


async def _list_suggestions(request: Request) -> Response:
    root = _root(request)
    rows = [_list_row(doc) for doc in proposals.load_all_proposals(root)]
    result = recommend_metrics.compute_metrics(root)
    context = {"rows": rows, "metrics": _metrics_context(result)}
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
    root = _root(request)
    slug = request.path_params["slug"]
    doc = proposals.load_proposal(root, slug)
    if doc is None:
        return _error_response(request, 404, f"proposal {slug!r} not found or malformed")

    scores = doc.get("scores") if isinstance(doc.get("scores"), dict) else {}
    context: dict[str, Any] = {
        **_base_context(request),
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
    return _templates(request).TemplateResponse(request, "suggestion_detail.html", context)


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
    root = _root(request)
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
    root = _root(request)
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


# --- GET/POST /suggestions/{slug}/edit --------------------------------------


async def _edit_view(request: Request) -> Response:
    root = _root(request)
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
    if not proposals.save_edited_draft(root, slug, draft):
        return _error_response(request, 400, "could not save edited draft")
    return _redirect_with_flash(slug, "Draft updated.")


# --- Sessions (Phase 2b) ----------------------------------------------------


def _redact_display(text: str) -> str:
    """Redact at display time on every raw surface — a raw is scrubbed at capture
    (D13), but a legacy/hand-edited capture may carry secrets the scribe never
    saw, so re-scrub with the same pass every write path uses (§14 defense)."""
    return redact.redact(text, load_config().redact.extra_patterns)


def _capture_path(raw_dir: Path, file: str) -> Path | None:
    """Resolve a capture filename to a real file **directly inside** ``raw_dir``,
    or ``None``. Defense-in-depth: ``{file}`` is a single URL segment (Starlette
    won't match a ``/``), but a crafted ``..``-bearing name is refused here rather
    than trusted — the resolved path must have ``raw_dir`` as its immediate parent,
    so it can neither escape the project's ``raw/`` nor dip into a subdirectory.
    ``raw_dir`` must already be ``.resolve()``-d by the caller."""
    path = (raw_dir / file).resolve()
    if path.parent != raw_dir or not path.is_file():
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


async def _list_sessions(request: Request) -> Response:
    root = _root(request)
    handle = open_store(root, StoreMode.READ)
    rows: list[dict[str, Any]] = []
    for project in handle.load_registry():
        try:
            for doc in handle.list_raw(project, unconsumed_only=False):
                rows.append(_session_row(project, doc))
        except (OSError, ValueError):
            continue  # one unreadable project must not blank the whole surface
    # Newest capture first — most recent activity at the top of a review list.
    rows.sort(key=lambda r: str(r["captured_at"] or ""), reverse=True)
    unconsumed = sum(1 for r in rows if not r["consumed"])
    context = {"rows": rows, "unconsumed": unconsumed}
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
    path = _capture_path(raw_dir, file)
    if path is None:
        return _error_response(request, 404, "No such capture.")
    try:
        doc = store.read_doc(path)
    except (ValueError, OSError):
        return _error_response(request, 404, "This capture is unreadable.")
    session_id = str(doc.get("session_id") or "")
    context = {
        "project": project,
        "session": session_id[:8] or "—",
        "session_full": session_id,
        "agent": str(doc.get("agent") or "—"),
        "branch": str(doc.get("branch") or "—"),
        "cwd": str(doc.get("cwd") or "—"),
        "captured_at": doc.get("captured_at"),
        "consumed": bool(doc.get("consumed")),
        "body": _redact_display(doc.body),
    }
    return _templates(request).TemplateResponse(request, "session_detail.html", context)


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


async def _list_memory(request: Request) -> Response:
    root = _root(request)
    handle = open_store(root, StoreMode.READ)
    groups: list[dict[str, Any]] = []
    for project in sorted(handle.load_registry()):
        try:
            facts = [_fact_row(project, d) for d in handle.list_curated(project, active_only=True)]
        except (OSError, ValueError):
            continue  # one unreadable project must not blank the whole surface
        facts.sort(key=lambda f: str(f["updated_at"] or ""), reverse=True)
        groups.append({"project": project, "nodes": _read_nodes(handle, project), "facts": facts})
    total = sum(len(g["facts"]) for g in groups)
    context = {"groups": groups, "total": total}
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
    if not path.is_file():
        return _error_response(request, 404, "No such fact.")
    try:
        doc = store.read_doc(path)
    except (ValueError, OSError):
        return _error_response(request, 404, "This fact is unreadable.")
    provenance = doc.get("provenance") if isinstance(doc.get("provenance"), list) else []
    supersedes = doc.get("supersedes") if isinstance(doc.get("supersedes"), list) else []
    context = {
        "project": project,
        "slug": str(doc.get("name") or slug),
        "status": str(doc.get("status") or "active"),
        "agent_last": str(doc.get("agent_last") or "—"),
        "updated_at": doc.get("updated_at"),
        "provenance": [_prov_link(project, p) for p in provenance],
        "supersedes": [str(s) for s in supersedes],
        "body": _redact_display(doc.body),
    }
    return _templates(request).TemplateResponse(request, "memory_detail.html", context)


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


__all__ = ["all_routes", "memory_routes", "sessions_routes", "suggestions_routes"]
