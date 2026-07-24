"""Codex scribe (spec §5): the per-turn rollout capture.

Codex has no SessionEnd — hooks fire per turn — so this scribe is written to run
on every turn and **overwrite one raw file per session in place**: it passes
``captured_at = session start timestamp`` to the raw write, so the derived
filename is stable and each firing's atomic write replaces the prior one
(last-turn-wins). Deterministic, no LLM, **every code path exits 0** (never wedge
a turn). Parses a Codex rollout (JSONL, §11.2), redacts (D13), and writes one raw
capture with ``agent: codex`` — but only if the resolved project's memory tree
exists (opt-in), and only if the capture is non-empty.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neurobase.adapters.scribe_common import (
    MAX_ASSISTANT_MSG_CHARS,
    MAX_ASSISTANT_TOTAL_CHARS,
    Redactor,
    block,
    bounded_highlights,
    bullet,
    final_summary,
)
from neurobase.core import store
from neurobase.core.config import load_config
from neurobase.core.enable import resolve_or_auto_enable
from neurobase.core.redact import redact
from neurobase.core.store_handle import StoreMode, open_store

__all__ = [
    "MAX_ASSISTANT_MSG_CHARS",
    "MAX_ASSISTANT_TOTAL_CHARS",
    "discover_rollout",
    "parse_rollout",
    "scribe",
]

# Tuned defaults (spec §8) — identical to the Claude scribe; §8 is agent-agnostic.
# The assistant-highlight bounds and their eviction come from scribe_common, so
# the two scribes cannot drift on one shared contract.
MAX_PROMPTS = 25
MAX_PROMPT_CHARS = 1200
MAX_SUMMARY_CHARS = 4000
# The latest IDE context block, kept once as session metadata (spec §5).
MAX_IDE_CHARS = 800

# VS Code extension wraps typed prompts (spec §5). Split at the request marker;
# keep the request as the prompt and the preceding block as IDE context.
_IDE_CONTEXT_MARKER = "# Context from my IDE setup:"
_IDE_REQUEST_MARKER = "## My request for Codex:"

_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"


def _iter_events(rollout_path: Path) -> list[dict[str, Any]]:
    """Parse the JSONL rollout, skipping unparseable lines (never fatal)."""
    events: list[dict[str, Any]] = []
    try:
        text = rollout_path.read_text(encoding="utf-8")
    except OSError:
        return events
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _split_ide_wrapper(message: str) -> tuple[str, str | None]:
    """Split a VS Code-wrapped prompt into ``(prompt, ide_context)``. A plain
    (non-wrapped) message returns ``(message, None)``."""
    if _IDE_REQUEST_MARKER not in message:
        return message, None
    before, _, after = message.partition(_IDE_REQUEST_MARKER)
    prompt = after.strip()
    context = before.strip()
    if context.startswith(_IDE_CONTEXT_MARKER):
        context = context[len(_IDE_CONTEXT_MARKER) :].strip()
    return prompt, (context or None)


def parse_rollout(rollout_path: Path) -> dict[str, Any]:
    """Return ``{prompts, summary, highlights, ide_context, cwd, branch,
    session_id, started_at}`` from a rollout. ``prompts`` are the clean typed
    user turns (IDE wrapper stripped, consecutive duplicates dropped);
    ``summary`` is the longest of the last 3 agent messages and ``highlights``
    the bounded chronological rest (spec §5); ``started_at`` is the session_meta
    timestamp (ISO string) that keys the per-turn overwrite."""
    prompts: list[str] = []
    agent_messages: list[str] = []
    ide_context = ""
    cwd = ""
    branch = ""
    session_id = ""
    started_at = ""
    for event in _iter_events(rollout_path):
        if event.get("type") == "session_meta":
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            session_id = payload.get("session_id") or payload.get("id") or session_id
            cwd = payload.get("cwd") or cwd
            started_at = payload.get("timestamp") or started_at
            git = payload.get("git")
            if isinstance(git, dict):
                branch = git.get("branch") or branch
            continue
        if event.get("type") != "event_msg":
            continue  # response_item / turn_context / token_count → ignored
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        ptype = payload.get("type")
        if ptype == "user_message":
            message = payload.get("message")
            if not isinstance(message, str):
                continue
            prompt, ctx = _split_ide_wrapper(message)
            if ctx is not None:
                ide_context = ctx  # latest IDE context wins
            prompt = prompt.strip()
            if not prompt:
                continue
            if prompts and prompts[-1] == prompt:
                continue  # thread_rolled_back re-emits the previous prompt
            prompts.append(prompt)
        elif ptype == "agent_message":
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                agent_messages.append(message.strip())
    return {
        "prompts": prompts,
        "summary": final_summary(agent_messages),
        "highlights": bounded_highlights(agent_messages),
        "ide_context": ide_context,
        "cwd": cwd,
        "branch": branch,
        "session_id": session_id,
        "started_at": started_at,
    }


def _assemble_body(
    prompts: list[str], summary: str, ide_context: str, highlights: list[str], scrub: Redactor
) -> str:
    """Render the spec §5 body. As in §4, ``scrub`` (D13) runs on every captured
    value *before* rendering, and every value goes through the structural
    helpers — including the IDE context, which is a section body rather than a
    bullet but is just as capable of forging a heading."""
    kept = [scrub(p[:MAX_PROMPT_CHARS]) for p in prompts[-MAX_PROMPTS:]]
    lines = [
        "## Session",
        "- agent: codex",
        f"- prompts captured: {len(kept)}",
    ]
    if ide_context:
        ide = block(scrub(ide_context[:MAX_IDE_CHARS]))
        lines += ["", "## Files in focus (IDE)", "", ide]
    lines += ["", "## Prompts"]
    lines += [bullet(p) for p in kept]
    if highlights:
        lines += ["", "## Assistant highlights"]
        lines += [bullet(scrub(message)) for message in highlights]
    lines += ["", "## Final assistant summary", "", block(scrub(summary[:MAX_SUMMARY_CHARS]))]
    return "\n".join(lines)


def _parse_started_at(started_at: str) -> datetime:
    """Session-start ISO timestamp → aware datetime (keys the per-turn
    overwrite). Any parse failure falls back to ``now`` — capture still works,
    it just won't dedupe across turns for that session."""
    if started_at:
        try:
            return datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


def _read_session_meta(rollout_path: Path) -> dict[str, Any] | None:
    """The first-line ``session_meta`` payload, or ``None`` (never raises)."""
    try:
        with rollout_path.open(encoding="utf-8") as fh:
            first = fh.readline()
    except OSError:
        return None
    try:
        event = json.loads(first)
    except ValueError:
        return None
    if isinstance(event, dict) and event.get("type") == "session_meta":
        payload = event.get("payload")
        if isinstance(payload, dict):
            return payload
    return None


def discover_rollout(
    *,
    session_id: str | None = None,
    min_mtime: float | None = None,
    sessions_root: Path | None = None,
) -> Path | None:
    """Find the active rollout when the hook payload carries no path (the
    ``notify`` fallback never does — §11.4). Newest ``rollout-*.jsonl`` by mtime
    with ``mtime >= min_mtime``.

    ``session_id`` (the notify payload's thread id) is a **hard requirement**
    when given — return the newest eligible rollout whose ``session_meta``
    matches, else ``None`` (fail closed rather than capture an unrelated
    session's rollout into this project — spec §5/§11.4). A matching rollout is
    correct regardless of age (a resumed session's rollout can be old), so
    ``min_mtime`` is only a defensive floor the caller may supply; notify's
    payload carries no turn-start, so its capture relies on the id match. Only
    when no id is given (no cross-check possible) do we fall back to newest."""
    base = sessions_root or _SESSIONS_ROOT
    if not base.exists():
        return None
    try:
        candidates = sorted(
            base.glob("**/rollout-*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    eligible: list[Path] = []
    for path in candidates:
        try:
            if min_mtime is not None and path.stat().st_mtime < min_mtime:
                continue
        except OSError:
            continue
        eligible.append(path)
    if not eligible:
        return None
    if session_id is not None:
        for path in eligible:
            meta = _read_session_meta(path)
            if meta and session_id in (meta.get("session_id"), meta.get("id")):
                return path
        return None  # id given but nothing matched → fail closed
    return eligible[0]  # no id to cross-check → best-effort newest


def scribe(
    root: Path,
    *,
    rollout_path: Path,
    cwd: str = "",
    session_id: str = "",
) -> Path | None:
    """Write (or session-keyed-overwrite) one raw capture for a Codex turn, or
    ``None`` if there's no project tree / nothing to capture. Deterministic;
    callers should treat any exception as "capture nothing" (the hook exits 0)."""
    parsed = parse_rollout(rollout_path)

    # cwd from the hook payload takes precedence over the rollout's.
    resolve_cwd = Path(cwd or parsed["cwd"] or ".").expanduser()
    config = load_config()
    # Resolve the project — and, under folder-scoped auto-enable, register it +
    # create its tree when this repo sits under a configured auto_enable_root
    # (consent given once at the folder, not per repo). A too-new store fails
    # closed *inside* this call (→ None), so an untracked/opted-out capture still
    # never creates store.toml as a side effect (ADR-0015 D11).
    project = resolve_or_auto_enable(
        root,
        resolve_cwd,
        auto_enable_roots=config.enable.auto_enable_roots,
        denylist=config.enable.denylist,
    )
    if project is None:
        return None  # untracked directory (and not folder-scoped auto-enabled)
    # D11: re-inspect through a READ handle before the opt-in check. After
    # auto-enable the tree exists; a project registered but never given a tree
    # still no-ops here.
    try:
        handle = open_store(root, StoreMode.READ)
    except store.UnsupportedSchemaError:
        return None  # fail closed — never operate on an incompatible store
    if not handle.memory_dir(project).exists():
        return None  # opt-in: no tree ⇒ write nothing

    prompts: list[str] = parsed["prompts"]
    summary: str = parsed["summary"]
    if not prompts and not summary:
        return None  # empty capture ⇒ write nothing

    extra_patterns = config.redact.extra_patterns
    scrub: Redactor = lambda text: redact(text, extra_patterns)  # noqa: E731
    body = _assemble_body(
        prompts,
        summary,
        parsed["ide_context"],
        parsed["highlights"],
        scrub,
    )
    body = redact(body, extra_patterns)  # defense in depth over the whole document

    # D13 covers the whole raw, not just its body (spec §10): `cwd` and `branch`
    # are informational frontmatter, so they are scrubbed too. `session_id` is
    # NOT — it keys the filename and the per-turn overwrite trick, so rewriting
    # it would break dedupe. It is agent-generated, never user-authored text.
    sid = session_id or parsed["session_id"]
    started = _parse_started_at(parsed["started_at"])
    transcript = scrub(str(rollout_path))
    # Commit through a WRITE handle. The tree exists, so store.toml exists and this
    # only re-validates; a partial store (tree but no store.toml) is created here,
    # exactly as the old ensure_store_metadata guard did.
    writer = open_store(root, StoreMode.WRITE)
    try:
        return writer.write_raw(
            project,
            agent="codex",
            session_id=sid,
            cwd=scrub(str(resolve_cwd)),
            branch=scrub(parsed["branch"]),
            captured_at=started,
            body=body,
            transcript_path=transcript,
        )
    except store.RawConsumedError:
        # The session's raw was already folded mid-session; write a fresh
        # capture under a new filename (spec §1 mutability rule).
        return writer.write_raw(
            project,
            agent="codex",
            session_id=sid,
            cwd=scrub(str(resolve_cwd)),
            branch=scrub(parsed["branch"]),
            captured_at=datetime.now(UTC),
            body=body,
            transcript_path=transcript,
        )
