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

from neurobase.core import store
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
    unpreviewed bytes."""
    artifact = preview.artifact
    payload = "\0".join([str(artifact.path), str(artifact.target), artifact.before, artifact.after])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
        # Mirrors `recommend accept`'s own early return: nothing to install,
        # so nothing is written and the proposal's decision is left as-is.
        return _redirect_with_flash(slug, "Already up to date — nothing to install.")

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


__all__ = ["suggestions_routes"]
