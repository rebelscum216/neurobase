"""Curator (spec §2): the thinking loop that folds raw captures into a small,
current, non-redundant fact set — optimizing for deletion and supersession.

``curate(root, project, brain)`` runs the §2 sequence. The brain is passed in
(the injection point spec §2 mandates), so the whole apply pipeline is testable
with a fake brain and no network.

Hard rules preserved here:
- A plan that won't parse ⇒ ABORT, leave every raw unconsumed (decision D9).
- A *valid-but-empty* plan IS consumed (idempotence).
- Node synthesis / index failing *after* raws were consumed ⇒ ``partial`` (the
  node is a pure function of ``curated/`` and self-heals on any later pass).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neurobase.brain.base import Brain, BrainError
from neurobase.core import linkify, store

DEFAULT_TOMBSTONE_GRACE_DAYS = 14
NODE_SUFFIX = "-status"
CURATOR_LOG = ".curator-log.jsonl"

PLAN_SYSTEM = """\
You are the curator of a durable, cross-agent engineering memory. You are given \
the project's current CURATED FACTS and new RAW captures from coding-agent \
sessions. Your goal is a small, non-redundant, current fact set — optimize for \
deletion and merging, not accumulation.

Rules:
- Prefer updating an existing fact (reuse its slug) over writing a near-duplicate.
- When a new observation obsoletes a fact, write the corrected fact and list the \
replaced slug(s) in "supersedes".
- Tombstone stale facts that nothing replaces.
- A fact is one durable, self-contained statement — not a session log.
- Slugs are stable kebab-case matching ^[a-z0-9-]+$.
- Include only facts that change; omit unchanged ones.
- In each upsert's "from_raw", list the raw filenames the fact draws on.

Respond with ONLY a JSON object — no prose, no code fences — exactly of the form:
{"upserts": [{"slug": "...", "body": "...", "supersedes": ["..."], "from_raw": ["..."]}], \
"tombstones": [{"slug": "...", "reason": "..."}]}"""

NODE_SYSTEM = """\
You synthesize ONE status node from a project's active curated facts: concise, \
skimmable markdown that a teammate or a fresh agent session reads to get current \
fast. Start with a short title line, then grouped bullets under headings such as \
current work / recent decisions / gotchas & constraints / open threads. Use only \
what the facts support — invent nothing. Output markdown only: no preamble, no \
code fences."""


def node_name(project: str) -> str:
    return f"{project}{NODE_SUFFIX}"


def _facts_payload(docs: list[store.Document]) -> list[dict[str, str]]:
    return [{"slug": str(d.get("name", d.file_path.stem)), "body": d.body.strip()} for d in docs]


def _safe_soft_delete(root: Path, project: str, slug: str) -> bool:
    """Tombstone ``slug`` if it exists and is a valid slug; return whether it
    was. Missing files / bad slugs are skipped, never fatal."""
    try:
        store.soft_delete_curated(root, project, slug)
    except (FileNotFoundError, store.InvalidSlugError):
        return False
    return True


def _apply_upserts(
    root: Path, project: str, upserts: list[dict[str, Any]]
) -> tuple[set[str], list[str]]:
    """Apply upserts (spec §2 step 4). Returns (upserted slugs, superseded
    slugs to tombstone). Empty slug/body skipped; supersedes filtered of self;
    bad slug skipped with a warning."""
    upserted: set[str] = set()
    superseded: list[str] = []
    for upsert in upserts:
        slug = str(upsert.get("slug", "")).strip()
        body = str(upsert.get("body", "")).strip()
        if not slug or not body:
            continue
        supersedes = [s for s in (upsert.get("supersedes") or []) if s and s != slug]
        from_raw = [r for r in (upsert.get("from_raw") or []) if r]
        provenance = [f"raw/{name}" for name in from_raw]
        try:
            store.upsert_curated(
                root, project, slug, body, provenance=provenance, supersedes=supersedes
            )
        except store.InvalidSlugError:
            continue  # bad slug ⇒ skip + warn (the model occasionally emits one)
        upserted.add(slug)
        superseded.extend(str(s) for s in supersedes)
    return upserted, superseded


def _synthesize(root: Path, project: str, brain: Brain) -> None:
    """Regenerate the status node from active facts, rebuild the index, linkify
    (spec §2 step 8). Node is a pure function of ``curated/``."""
    active = store.list_curated(root, project)
    if not active:
        body = "# (no active facts)\n\n_Nothing curated yet._"
    else:
        payload = json.dumps({"active_facts": _facts_payload(active)}, ensure_ascii=False)
        body = _strip_outer_fence(brain.text(NODE_SYSTEM, payload).strip())
    store.write_node(root, project, node_name(project), body)
    store.rebuild_index(root, project)
    linkify.linkify(root, project)


def _strip_outer_fence(text: str) -> str:
    """Defensive: drop a single surrounding ```...``` fence if the model wraps
    its markdown despite being told not to."""
    if text.startswith("```") and text.endswith("```"):
        inner = text[3:-3]
        # drop an optional language tag on the opening fence line
        if "\n" in inner:
            first, rest = inner.split("\n", 1)
            if first.strip().isalpha():
                return rest.strip()
        return inner.strip()
    return text


def _log_pass(root: Path, project: str, summary: dict[str, Any]) -> None:
    mem = store.memory_dir(project, root)
    mem.mkdir(parents=True, exist_ok=True)
    record = {**summary, "at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}
    with (mem / CURATOR_LOG).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def curate(
    root: Path,
    project: str,
    brain: Brain,
    *,
    dry_run: bool = False,
    resynth: bool = False,
    tombstone_grace_days: int = DEFAULT_TOMBSTONE_GRACE_DAYS,
) -> dict[str, Any]:
    """Run one curate pass (spec §2). Returns the summary dict."""
    store.ensure_tree(project, root)

    if resynth:
        _synthesize(root, project, brain)
        summary = {"status": "resynth", "active_facts": len(store.list_curated(root, project))}
        _log_pass(root, project, summary)
        return summary

    raw_docs = store.list_raw(root, project, unconsumed_only=True)
    if not raw_docs:
        active = len(store.list_curated(root, project))
        summary = {"status": "noop", "raw": 0, "active_facts": active}
        _log_pass(root, project, summary)
        return summary

    curated = store.list_curated(root, project)
    user_payload = json.dumps(
        {
            "curated_facts": _facts_payload(curated),
            "raw_captures": [{"raw": d.file_path.name, "body": d.body.strip()} for d in raw_docs],
        },
        ensure_ascii=False,
    )

    # Step 2/3: plan. Unparseable ⇒ abort, leave every raw unconsumed.
    try:
        plan = brain.plan_json(PLAN_SYSTEM, user_payload)
    except BrainError as exc:
        summary = {"status": "error", "raw": len(raw_docs), "error": str(exc)}
        _log_pass(root, project, summary)
        return summary

    upserts = plan.get("upserts") or []
    tombstones = plan.get("tombstones") or []

    if dry_run:
        return {"status": "dry-run", "raw": len(raw_docs), "plan": plan}

    # Step 4: upserts + tombstone superseded (unless re-upserted this pass).
    upserted, superseded_slugs = _apply_upserts(root, project, upserts)
    superseded_count = sum(
        _safe_soft_delete(root, project, slug)
        for slug in dict.fromkeys(superseded_slugs)  # dedupe, order-preserving
        if slug not in upserted
    )

    # Step 5: explicit tombstones (skip any slug upserted this pass).
    tombstone_count = 0
    for entry in tombstones:
        slug = str(entry.get("slug", "")).strip()
        if not slug or slug in upserted:
            continue
        if _safe_soft_delete(root, project, slug):
            tombstone_count += 1

    # Step 6: mark all consumed raws consumed.
    for doc in raw_docs:
        store.mark_consumed(doc.file_path)

    # Step 7: prune tombstones past the grace period.
    pruned = store.prune_tombstones(root, project, older_than_days=tombstone_grace_days)

    # Step 8: node synthesis + index + linkify. Per spec §2's partial-failure
    # contract, ANY failure here — brain error, a malformed sibling node
    # tripping the index rebuild, a linkify/disk error — is `partial`, not a
    # crash: raws are already consumed and the applied state stands, and the
    # node self-heals on any later pass (it's a pure function of curated/).
    status = "ok"
    synth_error: str | None = None
    try:
        _synthesize(root, project, brain)
    except Exception as exc:  # noqa: BLE001 - spec §2: keep applied state, log, return partial
        status = "partial"
        synth_error = str(exc)

    summary = {
        "status": status,
        "raw": len(raw_docs),
        "upserts": len(upserted),
        "superseded": superseded_count,
        "tombstones": tombstone_count,
        "pruned_tombstones": len(pruned),
        "active_facts": len(store.list_curated(root, project)),
    }
    if synth_error is not None:
        summary["error"] = synth_error
    _log_pass(root, project, summary)
    return summary


def is_stale(root: Path, project: str, hours: int) -> bool:
    """True if any unconsumed raw is older than ``hours`` (decision D8's
    ``--if-stale`` gate)."""
    cutoff = datetime.now(UTC).timestamp() - hours * 3600
    for doc in store.list_raw(root, project, unconsumed_only=True):
        captured_at = doc.get("captured_at")
        if not captured_at:
            continue
        try:
            when = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.timestamp() < cutoff:
            return True
    return False


def read_fact_count_trend(root: Path, project: str, last: int = 5) -> list[int]:
    """The tail of the active-fact-count trend from the curator log (the bloat
    alarm `status` shows). Missing/short log ⇒ what's available."""
    log_path = store.memory_dir(project, root) / CURATOR_LOG
    if not log_path.exists():
        return []
    counts: list[int] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        value = record.get("active_facts")
        if isinstance(value, int):
            counts.append(value)
    return counts[-last:]
