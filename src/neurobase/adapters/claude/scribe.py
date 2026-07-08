"""Claude Code scribe (spec §4): the SessionEnd capture.

Deterministic, no LLM, **every code path exits 0** (never wedge teardown).
Parses a Claude Code transcript (JSONL, §11.1), redacts (D13), and writes one
raw capture — but only if the resolved project's memory tree exists (opt-in),
and only if the capture is non-empty.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neurobase.core import projects, store
from neurobase.core.config import load_config
from neurobase.core.redact import redact

# Tuned defaults (spec §8).
MAX_PROMPTS = 25
MAX_PROMPT_CHARS = 600
MAX_SUMMARY_CHARS = 4000

# Noise prefixes to drop from user turns (spec §4).
_NOISE_PREFIXES = (
    "<command-name>",
    "<local-command-",
    "<system-reminder>",
    "Caveat:",
    "[Request interrupted",
)


def _iter_events(transcript_path: Path) -> list[dict[str, Any]]:
    """Parse the JSONL transcript, skipping unparseable lines (never fatal)."""
    events: list[dict[str, Any]] = []
    try:
        text = transcript_path.read_text(encoding="utf-8")
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


def _text_from_content(content: Any) -> str | None:
    """Extract the typed user text: a plain string, or the joined ``text``
    blocks of a list. Return ``None`` if the turn contains a ``tool_result``
    block (those are skipped entirely, spec §4)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                return None  # skip the whole turn
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return None


def _assistant_text(content: Any) -> str:
    """Joined visible ``text`` blocks of an assistant turn (thinking/tool
    blocks excluded)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
    return ""


def _is_noise(text: str) -> bool:
    return text.startswith(_NOISE_PREFIXES)


def parse_transcript(transcript_path: Path) -> dict[str, Any]:
    """Return ``{prompts, summary, cwd, branch, session_id}`` from a transcript.
    Prompts are the clean typed user turns; summary is the last non-empty
    assistant text. Metadata rides on the user events."""
    prompts: list[str] = []
    summary = ""
    cwd = ""
    branch = ""
    session_id = ""
    for event in _iter_events(transcript_path):
        if event.get("isSidechain"):
            continue  # subagent turn
        etype = event.get("type")
        message = event.get("message")
        if etype == "user":
            cwd = event.get("cwd") or cwd
            branch = event.get("gitBranch") or branch
            session_id = event.get("sessionId") or session_id
            content = message.get("content") if isinstance(message, dict) else None
            text = _text_from_content(content)
            if text is None:
                continue  # tool_result turn
            text = text.strip()
            if not text or _is_noise(text):
                continue
            prompts.append(text)
        elif etype == "assistant":
            content = message.get("content") if isinstance(message, dict) else None
            text = _assistant_text(content).strip()
            if text:
                summary = text  # last non-empty wins
    return {
        "prompts": prompts,
        "summary": summary,
        "cwd": cwd,
        "branch": branch,
        "session_id": session_id,
    }


def _assemble_body(prompts: list[str], summary: str, reason: str) -> str:
    kept = prompts[-MAX_PROMPTS:]
    kept = [p[:MAX_PROMPT_CHARS] for p in kept]
    summary = summary[:MAX_SUMMARY_CHARS]
    lines = [
        "## Session",
        f"- ended: {reason}",
        f"- prompts captured: {len(kept)}",
        "",
        "## Prompts",
    ]
    lines.extend(f"- {p}" for p in kept)
    lines.extend(["", "## Final assistant summary", "", summary])
    return "\n".join(lines)


def scribe(
    root: Path,
    *,
    transcript_path: Path,
    cwd: str,
    reason: str,
    session_id: str = "",
) -> Path | None:
    """Write one raw capture for a finished Claude session, or ``None`` if
    there's no project tree / nothing to capture. Deterministic; callers should
    treat any exception as "capture nothing" (the hook wrapper exits 0)."""
    parsed = parse_transcript(transcript_path)

    # cwd from the hook payload takes precedence over the transcript's.
    resolve_cwd = Path(cwd or parsed["cwd"] or ".").expanduser()
    project = projects.resolve_project(root, resolve_cwd)
    if project is None:
        return None  # untracked directory
    if not store.memory_dir(project, root).exists():
        return None  # opt-in: no tree ⇒ write nothing
    try:
        store.ensure_store_metadata(root)  # D11: refuse a newer-schema store
    except store.UnsupportedSchemaError:
        return None  # fail closed — never write into an incompatible store

    prompts: list[str] = parsed["prompts"]
    summary: str = parsed["summary"]
    if not prompts and not summary:
        return None  # empty capture ⇒ write nothing

    body = _assemble_body(prompts, summary, reason)
    extra_patterns = load_config().redact.extra_patterns
    body = redact(body, extra_patterns)

    sid = session_id or parsed["session_id"]
    return store.write_raw(
        root,
        project,
        agent="claude",
        session_id=sid,
        cwd=str(resolve_cwd),
        branch=parsed["branch"],
        captured_at=datetime.now(UTC),
        body=body,
    )
