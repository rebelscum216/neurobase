"""Curator (spec §2): the thinking loop that folds raw captures into a small,
current, non-redundant fact set — optimizing for deletion and supersession.

``curate(root, project, brain)`` runs the §2 sequence. The brain is passed in
(the injection point spec §2 mandates), so the whole apply pipeline is testable
with a fake brain and no network.

Hard rules preserved here:
- A first plan that won't parse ⇒ ABORT, leave every raw unconsumed (D9).
- With D22 batching, a later failed batch and all later raws stay unconsumed;
  earlier valid batches remain applied and consumed — and because they are
  applied, the pass still prunes and re-synthesizes before returning its error,
  so the node recall injects never lags the facts that did commit.
- A *valid-but-empty* plan IS consumed (idempotence).
- Node synthesis / index failing *after* raws were consumed ⇒ ``partial`` (the
  node is a pure function of ``curated/`` and self-heals on any later pass).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neurobase.brain.base import Brain, BrainError, combine_prompt
from neurobase.core import linkify, store
from neurobase.curator import budget as budget_mod
from neurobase.curator import distill as distill_mod

DEFAULT_TOMBSTONE_GRACE_DAYS = 14
DEFAULT_PLAN_PAYLOAD_MAX_BYTES = 262_144
DEFAULT_DISTILL = "auto"
DEFAULT_DISTILL_CHUNK_CHARS = distill_mod.DISTILL_CHUNK_CHARS
OVERSIZE_RAW_MARKER = "\n\n[truncated for plan payload]"
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
- A fact marked "pinned": true was saved by explicit user direction — NEVER \
tombstone, supersede, or reword it; carry it forward unchanged.
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


def _facts_payload(docs: list[store.Document]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for d in docs:
        entry: dict[str, Any] = {
            "slug": str(d.get("name", d.file_path.stem)),
            "body": d.body.strip(),
        }
        if "user-directed" in (d.get("provenance") or []):
            entry["pinned"] = True  # spec §2: explicit user save — see PLAN_SYSTEM
        payload.append(entry)
    return payload


def _raw_payload(doc: store.Document, body: str | None = None) -> dict[str, str]:
    return {"raw": doc.file_path.name, "body": doc.body.strip() if body is None else body}


def _plan_user_payload(curated: list[store.Document], raw_captures: list[dict[str, str]]) -> str:
    """Serialize one plan payload. Kept as a single helper so byte budgeting
    measures the exact user string passed to ``brain.plan_json``."""
    return json.dumps(
        {"curated_facts": _facts_payload(curated), "raw_captures": raw_captures},
        ensure_ascii=False,
    )


def _plan_request_bytes(user_payload: str) -> int:
    """Size of the final prompt used by CLI brains, in UTF-8 bytes.

    API brains keep system/user separate, but using the combined CLI shape gives
    every backend the same conservative budget and protects the argv boundary.
    """
    return len(combine_prompt(PLAN_SYSTEM, user_payload).encode("utf-8"))


def _truncate_raw_to_fit(
    curated: list[store.Document], doc: store.Document, max_bytes: int
) -> dict[str, str]:
    """Return a marked raw entry that fits by truncating its body.

    Binary-search character boundaries while measuring serialized UTF-8 bytes;
    character counts are not a safe proxy for argv size. Raise when curated
    facts + envelope alone already consume the configured budget.
    """
    body = doc.body.strip()
    low, high = 0, len(body)
    best: dict[str, str] | None = None
    while low <= high:
        middle = (low + high) // 2
        candidate = _raw_payload(doc, body[:middle].rstrip() + OVERSIZE_RAW_MARKER)
        size = _plan_request_bytes(_plan_user_payload(curated, [candidate]))
        if size <= max_bytes:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        raise ValueError(
            "plan payload budget is too small for the system prompt, curated facts, "
            "and one truncated raw envelope"
        )
    return best


def _next_plan_batch(
    curated: list[store.Document],
    remaining: list[store.Document],
    max_bytes: int,
) -> tuple[list[store.Document], str]:
    """Build the next oldest-first batch within the final request-byte cap."""
    if max_bytes <= 0:
        raise ValueError("plan payload byte budget must be positive")
    docs: list[store.Document] = []
    entries: list[dict[str, str]] = []
    for doc in remaining:
        entry = _raw_payload(doc)
        candidate_entries = [*entries, entry]
        payload = _plan_user_payload(curated, candidate_entries)
        if _plan_request_bytes(payload) <= max_bytes:
            docs.append(doc)
            entries = candidate_entries
            continue
        if docs:
            break
        # Never silently skip one raw larger than the cap.
        docs.append(doc)
        entries = [_truncate_raw_to_fit(curated, doc, max_bytes)]
        break
    if not docs:  # defensive: callers only invoke this with remaining raws
        raise ValueError("cannot build an empty plan batch")
    return docs, _plan_user_payload(curated, entries)


def _pinned_slugs(docs: list[store.Document]) -> set[str]:
    """Slugs the curator must not tombstone, supersede, or reword — facts the
    user saved by explicit direction (provenance ``user-directed``, spec §2).
    Enforced deterministically in ``curate`` (not just via the prompt)."""
    return {
        str(d.get("name", d.file_path.stem))
        for d in docs
        if "user-directed" in (d.get("provenance") or [])
    }


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
    plan_payload_max_bytes: int = DEFAULT_PLAN_PAYLOAD_MAX_BYTES,
    distill: str = DEFAULT_DISTILL,
    distill_chunk_chars: int = DEFAULT_DISTILL_CHUNK_CHARS,
    redact_patterns: tuple[str, ...] = (),
    pass_budget: budget_mod.PassBudget | None = None,
) -> dict[str, Any]:
    """Run one curate pass (spec §2). Returns the summary dict.

    ``pass_budget`` bounds the pass (P0, 2026-07-17 runaway incident). Omitting
    it builds the permissive explicit-tier default, so a direct caller is still
    bounded — there is no unbounded path — while the CLI passes the much
    smaller automatic tier for hook-triggered runs.
    """
    store.ensure_tree(project, root)
    if pass_budget is None:
        pass_budget = budget_mod.explicit_budget()

    # P0 pass budget (Codex F1): wrapped ONCE, here, before either branch below.
    # `resynth` used to return before this rebind, leaving it a genuinely
    # unbudgeted brain call — the one path that contradicted "every call site
    # must debit the ledger". Wrapping before the branch closes that.
    budgeted = budget_mod.BudgetedBrain(brain, pass_budget)
    brain = budgeted

    if resynth:
        try:
            _synthesize(root, project, brain)
        except budget_mod.BudgetExhausted:
            # The budget stopped the resynth: `active_facts` may be stale
            # relative to what a completed resynth would have produced.
            # `partial` reuses the meaning the main path already gives that
            # word rather than claiming `resynth` succeeded (Codex F1).
            summary = {
                "status": "partial",
                "active_facts": len(store.list_curated(root, project)),
                "error": "budget exhausted before resynth completed",
                **pass_budget.summary(),
            }
            _log_pass(root, project, summary)
            return summary
        summary = {"status": "resynth", "active_facts": len(store.list_curated(root, project))}
        _log_pass(root, project, summary)
        return summary

    raw_docs = store.list_raw(root, project, unconsumed_only=True)
    if not raw_docs:
        active = len(store.list_curated(root, project))
        summary = {"status": "noop", "raw": 0, "active_facts": active}
        _log_pass(root, project, summary)
        return summary

    # Every brain call from here on goes through the wrapper bound above, so no
    # call site — including one added later — can escape the ledger. Raws
    # beyond the ceiling are dropped BEFORE the batch loop, which is what makes
    # "the rest stays unconsumed" structural: they never reach `mark_consumed`.
    backlog = len(raw_docs)
    raw_docs = pass_budget.select_raws(raw_docs)

    # Step 1 (spec §2.0): distill each raw's transcript into a richer body for
    # this pass, degrading to the skim on any failure (D16 — never aborts). The
    # cache is derived state; a dry run reads it but never writes it.
    raw_docs, distill_counts = distill_mod.distill_docs(
        root,
        project,
        raw_docs,
        budgeted.for_distill(),
        mode=distill,
        chunk_chars=distill_chunk_chars,
        extra_patterns=redact_patterns,
        write_cache=not dry_run,
    )

    remaining = list(raw_docs)
    plans: list[dict[str, Any]] = []
    batch_count = 0
    upsert_count = 0
    superseded_count = 0
    tombstone_count = 0
    plan_error: str | None = None

    while remaining:
        # Reload after every committed batch: later plans must see facts added,
        # updated, superseded, or tombstoned by earlier batches.
        curated = store.list_curated(root, project)
        try:
            batch_docs, user_payload = _next_plan_batch(curated, remaining, plan_payload_max_bytes)
        except ValueError as exc:
            plan_error = str(exc)
            break

        # Step 2/3 per batch. A failed batch and every later batch stay
        # unconsumed; earlier committed batches intentionally stand.
        try:
            plan = brain.plan_json(PLAN_SYSTEM, user_payload)
        except budget_mod.BudgetExhausted:
            # A ceiling, not a failure: stop cleanly and leave `remaining`
            # unconsumed. Deliberately NOT `plan_error` — that would report
            # `status: error`, which the CLI turns into exit 1 and would break
            # the hooks-always-exit-zero guarantee for a normal bounded stop.
            break
        except BrainError as exc:
            plan_error = str(exc)
            break

        plans.append(plan)
        batch_count += 1
        if dry_run:
            # A dry run never applies earlier plans, so later preview batches
            # intentionally use the current persisted facts rather than
            # pretending to simulate model-authored mutations.
            remaining = remaining[len(batch_docs) :]
            continue

        upserts = plan.get("upserts") or []
        tombstones = plan.get("tombstones") or []
        pinned = _pinned_slugs(curated)

        # D-b guard (spec §2): user-directed facts are pinned — drop any plan
        # step that would reword, supersede, or tombstone one.
        upserts = [u for u in upserts if str(u.get("slug", "")).strip() not in pinned]

        # Step 4: upserts + tombstone superseded (unless re-upserted / pinned).
        upserted, superseded_slugs = _apply_upserts(root, project, upserts)
        upsert_count += len(upserted)
        superseded_count += sum(
            _safe_soft_delete(root, project, slug)
            for slug in dict.fromkeys(superseded_slugs)
            if slug not in upserted and slug not in pinned
        )

        # Step 5: explicit tombstones (skip a slug upserted in this batch or pinned).
        for entry in tombstones:
            slug = str(entry.get("slug", "")).strip()
            if not slug or slug in upserted or slug in pinned:
                continue
            if _safe_soft_delete(root, project, slug):
                tombstone_count += 1

        # Step 6 per batch: the plan was valid and its state is durable.
        for doc in batch_docs:
            store.mark_consumed(doc.file_path)
        remaining = remaining[len(batch_docs) :]

    if dry_run:
        if plan_error is not None:  # nothing was applied; report the failure
            summary = {
                "status": "error",
                "raw": len(raw_docs),
                "batches": batch_count,
                **distill_counts,
                "error": plan_error,
            }
            _log_pass(root, project, summary)
            return summary
        dry_summary: dict[str, Any] = {
            "status": "dry-run",
            "raw": len(raw_docs),
            "batches": batch_count,
            **distill_counts,
        }
        if len(plans) == 1:
            dry_summary["plan"] = plans[0]  # preserve the v0.1 single-batch API
        else:
            dry_summary["plans"] = plans
        return dry_summary

    if plan_error is not None and batch_count == 0:
        # Nothing was applied: the v0.1 abort, state for state (D9). No derived
        # state changed, so there is nothing to refresh. (The summary itself is
        # not byte-identical to v0.1's — it carries the new `batches: 0`.)
        summary = {
            "status": "error",
            "raw": len(raw_docs),
            "batches": 0,
            **distill_counts,
            "error": plan_error,
        }
        _log_pass(root, project, summary)
        return summary

    # Steps 7/8 run whenever at least one batch committed — including when a
    # LATER batch failed. Derived state must never lag committed facts: the node
    # is what recall injects, so skipping synthesis here would hide every fact
    # the successful batches wrote until some future pass happened to succeed —
    # and a permanently-failing raw would make that "never" (D22).
    pruned = store.prune_tombstones(root, project, older_than_days=tombstone_grace_days)

    # Per spec §2's partial-failure contract, ANY failure here — brain error, a
    # malformed sibling node tripping the index rebuild, a linkify/disk error —
    # is `partial`, not a crash: raws are already consumed and the applied state
    # stands, and the node self-heals on any later pass (it's a pure function of
    # curated/).
    # Codex F2: BudgetExhausted used to be caught separately here and silently
    # discarded, reporting `status: ok` even though synthesis never ran and the
    # node was left lagging the batches this pass just committed — exactly the
    # D22 hazard the comment above this block warns about. BudgetExhausted IS
    # already an Exception (it subclasses RuntimeError), so simply not
    # special-casing it lets the existing handler below catch it and report
    # `partial`, precisely the honest outcome: state changed, node not yet
    # refreshed, self-heals on a later pass. No new handling needed — the
    # special case was the bug.
    synth_error: str | None = None
    try:
        _synthesize(root, project, brain)
    except Exception as exc:  # noqa: BLE001 - spec §2: keep applied state, log, return partial
        synth_error = str(exc)

    if plan_error is not None:
        status = "error"  # the pass failed; the committed batches still stand
    elif synth_error is not None:
        status = "partial"
    else:
        status = "ok"

    # Raws the budget stopped short of, whether deferred before the pass or left
    # in `remaining` when a ceiling hit mid-loop. Reported so a bounded stop is
    # visible rather than looking like a completed pass.
    unconsumed_left = pass_budget.deferred_raws + len(remaining)

    summary = {
        "status": status,
        "raw": len(raw_docs),
        "backlog": backlog,
        "batches": batch_count,
        **distill_counts,
        "upserts": upsert_count,
        "superseded": superseded_count,
        "tombstones": tombstone_count,
        "pruned_tombstones": len(pruned),
        "active_facts": len(store.list_curated(root, project)),
        **pass_budget.summary(),
    }
    if unconsumed_left:
        summary["unconsumed_left"] = unconsumed_left
    if plan_error is not None:
        summary["error"] = plan_error
    if synth_error is not None:
        summary["synth_error" if plan_error is not None else "error"] = synth_error
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
