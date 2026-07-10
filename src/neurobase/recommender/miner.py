"""Miner (spec §12.5): ask an injectable ``Brain`` for durable-behavior
*candidates* over the read-only corpus — it never writes.

``mine(root, brain) -> list[dict]`` mirrors the curator's brain-injection
pattern (spec §2) exactly: build a system prompt + a JSON user payload from
``corpus.load_corpus`` (workstream C), call ``brain.plan_json`` (which reuses
``brain/base.py:parse_plan_json``'s lenient, fence-tolerant parser — hence the
response envelope is a JSON **object** ``{"candidates": [...]}``, not a bare
array), and return the structurally-valid candidates as plain dicts. The
ranker/proposal-store (workstream E) recomputes every count from each
candidate's ``evidence`` list and does all the writing; the miner is pure
read → propose.

Fail-soft throughout (spec §12.5, Invariants):

- An **unparseable** response or a genuine ``BrainError`` (timeout, exhausted
  retries) ⇒ ``mine()`` returns ``[]`` and the caller (``recommend run``,
  workstream F) leaves ``<root>/proposals/`` untouched — the same broad
  ``except BrainError`` the curator uses (``curator/engine.py:curate``). Because
  ``plan_json`` runs the parse inside its retry wrapper, an unparseable answer
  reaches us already as a ``BrainError``.
- A **structurally invalid** candidate (missing ``slug``/``draft``, bad slug,
  disallowed ``type``/``candidate_type``) is skipped with a logged warning, not
  fatal to the rest of the batch.

The prompt (§12.5) establishes the role, gates on ``min_occurrences``, forbids
proposing secrets, and feeds a compact, code-computed ledger summary (per-
``candidate_type`` reject counts + deduplicated rejected-proposal snippets, the
near-duplicate function from ``corpus`` selecting the representative set) with an
instruction not to re-propose them.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from neurobase.brain.base import Brain, BrainError
from neurobase.core import store
from neurobase.core.config import RecommendConfig, load_config
from neurobase.recommender import corpus

logger = logging.getLogger(__name__)

CANDIDATE_TYPES = frozenset(
    {"repeated-correction", "repeated-workflow", "repeated-instruction", "cross-project-convention"}
)
ARTIFACT_TYPES = frozenset({"skill", "rule"})

# A rejected-proposal snippet is truncated in the prompt — the model needs
# enough to recognize the shape, not the whole body.
_REJECTED_SNIPPET_CHARS = 400


def _system_prompt(min_occurrences: int) -> str:
    """The miner's system prompt (spec §12.5 — role, thresholds, secret ban,
    ledger-avoidance, JSON-only envelope). ``min_occurrences`` is interpolated
    so the gate the ranker later enforces (§12.6) is also stated to the model."""
    return f"""\
You mine a cross-agent engineering-memory corpus — curated facts and raw session \
captures from coding agents (Claude, Codex) across every project — for recurring, \
DURABLE behavior worth promoting into a reusable SKILL.md or a fenced \
AGENTS.md/CLAUDE.md rule block. You look for patterns, not one-off facts: a \
correction made repeatedly, a workflow followed across sessions, an instruction \
restated by different agents.

Rules:
- Propose only patterns evidenced at least {min_occurrences} times across the \
corpus (unless a fact is explicitly seeded as high-confidence). One-off \
observations are not candidates.
- NEVER propose secrets, credentials, API keys, tokens, or private personal \
content — not in a draft, a title, or a rationale.
- Do NOT re-propose anything similar to the REJECTED PROPOSALS listed in the \
ledger summary — the user already declined those.
- Cite your evidence: every candidate's "evidence" is a list of structured \
references to the corpus items it draws on, using exactly these shapes: \
{{"kind":"curated","project":"...","slug":"..."}}, \
{{"kind":"raw","project":"...","file":"..."}}, or {{"kind":"proposal","slug":"..."}}. \
The evidence list is the ground truth; do not inflate the self-reported counts.
- "slug" is stable kebab-case matching ^[a-z0-9-]+$. "type" is "skill" or \
"rule". "candidate_type" is one of: repeated-correction, repeated-workflow, \
repeated-instruction, cross-project-convention. "target" is one of AGENTS.md, \
CLAUDE.md, user-skill, project-skill.

Respond with ONLY a JSON object — no prose, no code fences — exactly of the form:
{{"candidates": [{{"slug": "...", "type": "rule", "candidate_type": "...", \
"title": "...", "rationale": "...", "draft": "...", "target": "AGENTS.md", \
"evidence": [...], "occurrences": 0, "projects": [], "agents": [], \
"supersedes": []}}]}}"""


def mine(
    root: Path,
    brain: Brain,
    *,
    config: RecommendConfig | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Run the miner over the corpus and return structurally-valid candidates
    (spec §12.5). Never writes; never raises past this call — an unparseable
    response or ``BrainError`` yields ``[]``. ``config``/``now`` are injectable
    for the corpus caps and testability."""
    cfg = config if config is not None else load_config().recommend
    loaded = corpus.load_corpus(root, config=cfg, now=now)
    user_payload = _build_payload(loaded, cfg)

    try:
        response = brain.plan_json(_system_prompt(cfg.min_occurrences), user_payload)
    except BrainError as exc:
        # Unparseable answer (surfaced as BrainError by plan_json's retry
        # wrapper) or a genuine timeout/exhausted-retry failure — either way,
        # return nothing so the caller leaves proposals/ untouched (§12.5).
        logger.warning("miner brain call failed, proposing nothing: %s", exc)
        return []

    raw_candidates = response.get("candidates") if isinstance(response, dict) else None
    if not isinstance(raw_candidates, list):
        # Parsed cleanly but not the {"candidates": [...]} envelope — treat as an
        # empty proposal set, not a crash.
        logger.warning("miner response had no 'candidates' list; proposing nothing")
        return []

    candidates: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_candidates):
        candidate = _validate_candidate(raw, index)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _validate_candidate(raw: Any, index: int) -> dict[str, Any] | None:
    """Return a normalized candidate dict, or ``None`` (with a logged warning)
    if it is structurally invalid: not an object, missing/blank ``slug`` or
    ``draft``, a slug that fails ``SLUG_RE``, or a disallowed ``type`` /
    ``candidate_type`` (spec §12.5)."""
    if not isinstance(raw, dict):
        logger.warning("miner candidate %d skipped: not an object", index)
        return None

    slug = str(raw.get("slug", "")).strip()
    if not slug or not store.SLUG_RE.match(slug):
        logger.warning("miner candidate %d skipped: missing or invalid slug %r", index, slug)
        return None

    draft = str(raw.get("draft", "")).strip()
    if not draft:
        logger.warning("miner candidate %r skipped: missing draft", slug)
        return None

    artifact_type = str(raw.get("type", "")).strip()
    if artifact_type not in ARTIFACT_TYPES:
        logger.warning("miner candidate %r skipped: invalid type %r", slug, artifact_type)
        return None

    candidate_type = str(raw.get("candidate_type", "")).strip()
    if candidate_type not in CANDIDATE_TYPES:
        logger.warning(
            "miner candidate %r skipped: invalid candidate_type %r", slug, candidate_type
        )
        return None

    return {
        "slug": slug,
        "type": artifact_type,
        "candidate_type": candidate_type,
        "title": str(raw.get("title", "")).strip(),
        "rationale": str(raw.get("rationale", "")).strip(),
        "draft": draft,
        "target": str(raw.get("target", "")).strip(),
        "evidence": _normalize_evidence(raw.get("evidence")),
        # Self-reported counts are advisory display only — the ranker (§12.6)
        # recomputes the real numbers from `evidence`. Carried through, coerced.
        "occurrences": _as_int(raw.get("occurrences")),
        "projects": _as_str_list(raw.get("projects")),
        "agents": _as_str_list(raw.get("agents")),
        "supersedes": _as_str_list(raw.get("supersedes")),
    }


def _normalize_evidence(value: Any) -> list[dict[str, str]]:
    """Keep only well-formed structured refs (§12.1); drop malformed entries.
    A malformed evidence item never fails the candidate — the ranker simply has
    less to count. Round-trips through ``corpus.EvidenceRef`` so the stored
    shape is exactly what proposal frontmatter expects."""
    if not isinstance(value, list):
        return []
    refs: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            refs.append(corpus.EvidenceRef.from_frontmatter(item).to_frontmatter())
        except (ValueError, KeyError):
            continue
    return refs


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


# --- prompt payload --------------------------------------------------------


def _build_payload(loaded: corpus.Corpus, cfg: RecommendConfig) -> str:
    """The JSON user payload the miner reasons over (spec §12.5): the corpus's
    curated facts + raw captures, plus the compact ledger summary."""
    payload = {
        "curated_facts": [
            {"project": f.project, "slug": f.slug, "body": f.body.strip()} for f in loaded.curated
        ],
        "raw_captures": [
            {
                "project": r.project,
                "file": r.file,
                "agent": r.agent,
                "session_id": r.session_id,
                "body": r.body.strip(),
            }
            for r in loaded.raw
        ],
        "ledger_summary": _ledger_summary_payload(loaded.ledger, cfg),
    }
    return json.dumps(payload, ensure_ascii=False)


def _ledger_summary_payload(ledger: corpus.LedgerSummary, cfg: RecommendConfig) -> dict[str, Any]:
    """Per-``candidate_type`` reject counts + a deduplicated set of rejected
    proposal snippets (spec §12.5). The near-duplicate function (§12.4/D18)
    selects which snippets survive — near-identical rejections collapse to one
    representative so the prompt stays compact instead of listing ten variants
    of the same declined idea."""
    return {
        "reject_counts_by_type": dict(ledger.reject_counts),
        "rejected_proposals": [
            {
                "slug": rp.slug,
                "candidate_type": rp.candidate_type,
                "snippet": rp.body.strip()[:_REJECTED_SNIPPET_CHARS],
            }
            for rp in _dedupe_rejected(ledger.rejected_proposals, cfg.near_duplicate_threshold)
        ],
    }


def _dedupe_rejected(
    rejected: list[corpus.RejectedProposal], threshold: float
) -> list[corpus.RejectedProposal]:
    """Collapse near-duplicate rejected proposals to representatives via the
    shared Jaccard near-duplicate check (``corpus.is_near_duplicate``), keeping
    stable order — this is §12.4's near-duplicate function selecting which
    rejected snippets reach the prompt."""
    kept: list[corpus.RejectedProposal] = []
    for rp in rejected:
        if any(corpus.is_near_duplicate(rp.body, k.body, threshold) for k in kept):
            continue
        kept.append(rp)
    return kept
