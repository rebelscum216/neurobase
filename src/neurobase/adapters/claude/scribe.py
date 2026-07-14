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

from neurobase.adapters.scribe_common import (
    MAX_ASSISTANT_MSG_CHARS,
    MAX_ASSISTANT_TOTAL_CHARS,
    bounded_highlights,
    bullet,
    final_summary,
)
from neurobase.core import projects, store
from neurobase.core.config import load_config
from neurobase.core.redact import redact

__all__ = [
    "MAX_ASSISTANT_MSG_CHARS",
    "MAX_ASSISTANT_TOTAL_CHARS",
    "parse_transcript",
    "scribe",
]

# Tuned defaults (spec §8). MAX_ASSISTANT_MSG_CHARS / MAX_ASSISTANT_TOTAL_CHARS
# are agent-agnostic and re-exported above from scribe_common, so both scribes
# enforce one contract; the rest are Claude-side (§8 marks them claude scribe).
MAX_PROMPTS = 25
MAX_PROMPT_CHARS = 1200
MAX_SUMMARY_CHARS = 4000
MAX_SUBAGENTS = 5
MAX_SUBAGENT_CHARS = 1500
MAX_ACTIVITY_FILES = 30
MAX_ACTIVITY_COMMANDS = 20
MAX_ACTIVITY_COMMAND_CHARS = 120

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


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


def _is_noise(text: str) -> bool:
    return text.startswith(_NOISE_PREFIXES)


def parse_transcript(transcript_path: Path) -> dict[str, Any]:
    """Parse a transcript into the spec §4 skim.

    Prompts are the clean typed user turns. The final summary is the longest of
    the last 3 non-empty assistant texts — the last one often being a throwaway
    reply, it is not a reliable summary on its own. Bounded assistant
    highlights, `Agent`/`Task` subagent reports, and a tool-activity digest ride
    alongside it; metadata rides on the user events."""
    prompts: list[str] = []
    assistant_messages: list[str] = []
    summary_candidates: list[str] = []
    task_ids: set[str] = set()
    subagent_reports: list[str] = []
    activity_files: list[str] = []
    activity_commands: list[str] = []
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
            if event.get("isCompactSummary") is True:
                compact = _text_from_content(content)
                if compact and compact.strip():
                    assistant_messages.append(compact.strip())
                continue
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    if block.get("tool_use_id") not in task_ids:
                        continue
                    report = _tool_result_text(block.get("content")).strip()
                    if report:
                        subagent_reports.append(report[:MAX_SUBAGENT_CHARS])
                        del subagent_reports[:-MAX_SUBAGENTS]  # keep the last N
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
                assistant_messages.append(text)
                summary_candidates.append(text)
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name")
                    tool_id = block.get("id")
                    tool_input = block.get("input")
                    if name in {"Agent", "Task"} and isinstance(tool_id, str):
                        task_ids.add(tool_id)
                    if not isinstance(tool_input, dict):
                        continue
                    # Best-effort: an odd or empty input is skipped, never fatal.
                    if name in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
                        path = tool_input.get("file_path")
                        if (
                            isinstance(path, str)
                            and len(activity_files) < MAX_ACTIVITY_FILES
                            and path not in activity_files
                        ):
                            activity_files.append(path)
                    elif name == "Bash":
                        command = tool_input.get("command")
                        lines = command.splitlines() if isinstance(command, str) else []
                        first_line = lines[0][:MAX_ACTIVITY_COMMAND_CHARS] if lines else ""
                        if (
                            first_line
                            and len(activity_commands) < MAX_ACTIVITY_COMMANDS
                            and first_line not in activity_commands
                        ):
                            activity_commands.append(first_line)
    return {
        "prompts": prompts,
        "summary": final_summary(summary_candidates),
        "highlights": bounded_highlights(assistant_messages),
        "subagent_reports": subagent_reports,
        "activity_files": activity_files,
        "activity_commands": activity_commands,
        "cwd": cwd,
        "branch": branch,
        "session_id": session_id,
    }


def _assemble_body(parsed: dict[str, Any], reason: str) -> str:
    prompts: list[str] = parsed["prompts"]
    summary: str = parsed["summary"]
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
    lines.extend(bullet(p) for p in kept)
    files: list[str] = parsed["activity_files"]
    commands: list[str] = parsed["activity_commands"]
    if files or commands:
        lines.extend(["", "## Activity"])
        if files:
            lines.extend(["", "### Files touched", *[bullet(path) for path in files]])
        if commands:
            lines.extend(["", "### Commands run", *[bullet(cmd) for cmd in commands]])
    reports: list[str] = parsed["subagent_reports"]
    if reports:
        lines.extend(["", "## Subagent reports"])
        lines.extend(bullet(report) for report in reports)
    highlights: list[str] = parsed["highlights"]
    if highlights:
        lines.extend(["", "## Assistant highlights"])
        lines.extend(bullet(highlight) for highlight in highlights)
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
    if not any(
        (
            prompts,
            summary,
            parsed["highlights"],
            parsed["subagent_reports"],
            parsed["activity_files"],
            parsed["activity_commands"],
        )
    ):
        return None  # empty capture ⇒ write nothing

    body = _assemble_body(parsed, reason)
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
