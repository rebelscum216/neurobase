"""Tests for the curator distill step (spec §2.0, ADR-0014).

Everything runs networkless through an injected fake brain — the same injection
point the plan/node steps use.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from neurobase.brain.base import BrainError
from neurobase.core import store
from neurobase.curator import distill, engine

_GOOD_DIGEST = (
    "## Decisions\n- Chose X over Y because Z.\n\n"
    "## State changes\n- Edited auth.py; ran tests: 12 passed.\n\n"
    "## Unresolved\n- Follow-up on the null check."
)


class DistillBrain:
    """Fake brain whose text() serves a scripted digest (or exception), and
    counts distill vs node calls by which system prompt it sees."""

    name = "fake"

    def __init__(self, digest: Any = _GOOD_DIGEST, node_text: str = "# Status") -> None:
        self._digest = digest
        self._node_text = node_text
        self.distill_calls = 0
        self.node_calls = 0
        self.plan_calls = 0

    def plan_json(self, system: str, user: str) -> dict:
        self.plan_calls += 1
        return {"upserts": [], "tombstones": []}

    def text(self, system: str, user: str) -> str:
        if system.startswith("You compress") or system.startswith("You merge"):
            self.distill_calls += 1
            if isinstance(self._digest, Exception):
                raise self._digest
            return self._digest
        self.node_calls += 1
        return self._node_text


def _write_transcript(path: Path, events: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


def _claude_events(text: str = "Fix the login bug", answer: str = "Fixed it in auth.py") -> list:
    return [
        {"type": "user", "message": {"role": "user", "content": text}},
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": answer}]},
        },
    ]


def _write_raw(
    root: Path,
    project: str,
    name: str,
    *,
    body: str = "skim body",
    agent: str = "claude",
    transcript_path: str | None = None,
    capture_version: int | None = None,
) -> store.Document:
    store.ensure_tree(project, root)
    fm: dict[str, Any] = {
        "agent": agent,
        "session_id": "s1",
        "cwd": "/x",
        "branch": "main",
        "captured_at": "2026-07-07T12:00:00Z",
        "consumed": False,
    }
    if transcript_path is not None:
        fm["transcript_path"] = transcript_path
        fm["capture_version"] = capture_version if capture_version is not None else 2
    path = store.memory_dir(project, root) / "raw" / name
    store.write_doc(path, fm, body)
    return store.read_doc(path)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "store"


# --- happy path: a real digest replaces the skim body --------------------


def test_distill_replaces_body_with_digest(root: Path, tmp_path: Path) -> None:
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", body="thin skim", transcript_path=str(t))
    brain = DistillBrain()
    out, counts = distill.distill_docs(root, "proj", [doc], brain)
    assert counts == {"distilled": 1, "fallback": 0}
    assert brain.distill_calls == 1
    assert out[0].body == _GOOD_DIGEST
    # provenance-critical fields are preserved on the substituted copy
    assert out[0].file_path == doc.file_path
    assert out[0].get("agent") == "claude"


# --- fallback matrix (D16: degrade, never abort) -------------------------


def test_off_mode_skips_entirely(root: Path, tmp_path: Path) -> None:
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", body="skim", transcript_path=str(t))
    brain = DistillBrain()
    out, counts = distill.distill_docs(root, "proj", [doc], brain, mode="off")
    assert counts == {"distilled": 0, "fallback": 0}
    assert brain.distill_calls == 0
    assert out[0].body == "skim"


def test_v1_raw_without_transcript_path_falls_back(root: Path) -> None:
    doc = _write_raw(root, "proj", "r1.md", body="skim")  # no transcript_path
    brain = DistillBrain()
    out, counts = distill.distill_docs(root, "proj", [doc], brain)
    assert counts == {"distilled": 0, "fallback": 1}
    assert brain.distill_calls == 0
    assert out[0].body == "skim"


def test_missing_transcript_falls_back(root: Path) -> None:
    doc = _write_raw(root, "proj", "r1.md", body="skim", transcript_path="/nope/gone.jsonl")
    brain = DistillBrain()
    out, counts = distill.distill_docs(root, "proj", [doc], brain)
    assert counts == {"distilled": 0, "fallback": 1}
    assert brain.distill_calls == 0
    assert out[0].body == "skim"


def test_brain_error_falls_back(root: Path, tmp_path: Path) -> None:
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", body="skim", transcript_path=str(t))
    brain = DistillBrain(digest=BrainError("distill blew up"))
    out, counts = distill.distill_docs(root, "proj", [doc], brain)
    assert counts == {"distilled": 0, "fallback": 1}
    assert out[0].body == "skim"


def test_refusal_shaped_output_falls_back(root: Path, tmp_path: Path) -> None:
    """The S-cf5 role-hijack: a conversational reply with no expected heading is
    a distill failure ⇒ skim (D16/F3)."""
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", body="skim", transcript_path=str(t))
    brain = DistillBrain(digest="I'm going to stop — what do you want instead? 1/2/3")
    out, counts = distill.distill_docs(root, "proj", [doc], brain)
    assert counts == {"distilled": 0, "fallback": 1}
    assert out[0].body == "skim"


def test_codex_transcript_falls_back_deferred_renderer(root: Path, tmp_path: Path) -> None:
    """Codex render is deferred (ADR-0013 S-cf3); a Codex raw degrades to skim
    without a brain call."""
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", body="skim", agent="codex", transcript_path=str(t))
    brain = DistillBrain()
    out, counts = distill.distill_docs(root, "proj", [doc], brain)
    assert counts == {"distilled": 0, "fallback": 1}
    assert brain.distill_calls == 0
    assert out[0].body == "skim"


# --- digest bounding (F1) ------------------------------------------------


def test_oversize_digest_is_hard_truncated(root: Path, tmp_path: Path) -> None:
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    huge = "## Decisions\n" + ("x" * (distill.DIGEST_MAX_CHARS + 5000))
    brain = DistillBrain(digest=huge)
    out, _ = distill.distill_docs(root, "proj", [doc], brain)
    assert len(out[0].body) <= distill.DIGEST_MAX_CHARS
    assert out[0].body.endswith(distill.DIGEST_TRUNC_MARKER)


# --- redaction (D17: per-value, before render) ---------------------------


def test_secret_in_tool_result_never_reaches_the_brain(root: Path, tmp_path: Path) -> None:
    """A planted secret in a tool_result must not appear in the text sent to the
    distiller (D17), nor in the cached digest / any store artifact."""
    secret = "API_TOKEN=supersecretvalue123"
    events = [
        {"type": "user", "message": {"role": "user", "content": "run the thing"}},
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": f"$ env\n{secret}\n"}
                ],
            },
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        },
    ]
    t = _write_transcript(tmp_path / "t.jsonl", events)

    captured: list[str] = []

    class CapturingBrain(DistillBrain):
        def text(self, system: str, user: str) -> str:
            if system.startswith("You compress") or system.startswith("You merge"):
                captured.append(user)
            return super().text(system, user)

    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    brain = CapturingBrain()
    out, counts = distill.distill_docs(root, "proj", [doc], brain)

    assert counts["distilled"] == 1
    assert captured, "distiller was called"
    assert "supersecretvalue123" not in captured[0]  # scrubbed before the brain saw it
    # and never in the cached digest sidecar
    cache = store.memory_dir("proj", root) / "raw" / ".digests" / "r1.md"
    assert "supersecretvalue123" not in cache.read_text(encoding="utf-8")


def test_secret_in_tool_use_command_never_reaches_the_brain(root: Path, tmp_path: Path) -> None:
    """A planted secret in an assistant ``tool_use`` *command* field must be
    scrubbed before the render reaches the distiller (D17). This drives the
    ``_tool_use_line`` path, whose command value goes through ``redact_command``
    (a *different* redactor than the tool_result path) — an env-assignment secret
    is exactly what that lexical boundary is meant to catch."""
    secret = "API_TOKEN=supersecretvalue123"
    events = [
        {"type": "user", "message": {"role": "user", "content": "set up the env"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {"command": f"export {secret} && env"},
                    }
                ],
            },
        },
    ]
    t = _write_transcript(tmp_path / "t.jsonl", events)

    captured: list[str] = []

    class CapturingBrain(DistillBrain):
        def text(self, system: str, user: str) -> str:
            if system.startswith("You compress") or system.startswith("You merge"):
                captured.append(user)
            return super().text(system, user)

    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    brain = CapturingBrain()
    out, counts = distill.distill_docs(root, "proj", [doc], brain)

    assert counts["distilled"] == 1
    assert captured, "distiller was called"
    rendered = captured[0]
    # The command line was rendered (proves _tool_use_line ran) but scrubbed.
    assert "[tool_use Bash] command=" in rendered
    assert "supersecretvalue123" not in rendered
    # and never in the cached digest sidecar
    cache = store.memory_dir("proj", root) / "raw" / ".digests" / "r1.md"
    assert "supersecretvalue123" not in cache.read_text(encoding="utf-8")


def test_tool_result_block_list_is_joined_and_scrubbed(root: Path, tmp_path: Path) -> None:
    """A ``tool_result`` whose ``content`` is a *list* of blocks (text blocks and
    bare strings) is joined by ``_result_text`` and scrubbed as one value before
    it reaches the distiller — the list branch, distinct from the string branch."""
    secret = "API_TOKEN=supersecretvalue123"
    events: list[dict] = [
        {"type": "user", "message": {"role": "user", "content": "run it"}},
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [
                            {"type": "text", "text": "first line of output"},
                            "a bare-string block",
                            {"type": "text", "text": secret},
                        ],
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        },
    ]
    t = _write_transcript(tmp_path / "t.jsonl", events)

    captured: list[str] = []

    class CapturingBrain(DistillBrain):
        def text(self, system: str, user: str) -> str:
            if system.startswith("You compress") or system.startswith("You merge"):
                captured.append(user)
            return super().text(system, user)

    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    out, counts = distill.distill_docs(root, "proj", [doc], CapturingBrain())

    assert counts["distilled"] == 1
    rendered = captured[0]
    # All three list blocks were joined and rendered under one tool_result label.
    assert "first line of output" in rendered
    assert "a bare-string block" in rendered
    # ...but the planted secret in the joined value was scrubbed.
    assert "supersecretvalue123" not in rendered


# --- chunking (oversize transcript) --------------------------------------


def test_oversize_transcript_drops_middle_chunks_and_marks_it(root: Path, tmp_path: Path) -> None:
    """When the render exceeds the chunk cap, the middle chunks are dropped (head
    + tail kept) and the digest is prefixed with a visible drop marker so the
    loss is never silent (``_chunk`` + the ``dropped`` path in ``_distill_one``)."""
    events = [
        {"type": "user", "message": {"role": "user", "content": "X" * 1000}},
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        },
    ]
    t = _write_transcript(tmp_path / "t.jsonl", events)

    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    # chunk_chars small ⇒ the ~1000-char render splits into far more than
    # MAX_DISTILL_CHUNKS (5) chunks, forcing the middle-drop.
    out, counts = distill.distill_docs(root, "proj", [doc], DistillBrain(), chunk_chars=50)

    assert counts["distilled"] == 1
    body = out[0].body
    assert "middle chunk(s) dropped for size" in body


# --- rendering (summary + subagent sidechain) ----------------------------


def test_summary_and_sidechain_events_are_rendered(root: Path, tmp_path: Path) -> None:
    """Compact-summary events and subagent sidechain turns both reach the render
    (the ``[compact summary]`` line and the ``(subagent)`` marker) — the richer
    distill is meant to include subagent context, so it must not be dropped."""
    events: list[dict] = [
        {"type": "summary", "summary": "earlier we fixed the login bug"},
        {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "sub task"}},
        {
            "type": "assistant",
            "isSidechain": True,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "sub answer"}]},
        },
    ]
    t = _write_transcript(tmp_path / "t.jsonl", events)

    captured: list[str] = []

    class CapturingBrain(DistillBrain):
        def text(self, system: str, user: str) -> str:
            if system.startswith("You compress") or system.startswith("You merge"):
                captured.append(user)
            return super().text(system, user)

    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    out, counts = distill.distill_docs(root, "proj", [doc], CapturingBrain())

    assert counts["distilled"] == 1
    rendered = captured[0]
    assert "[compact summary] earlier we fixed the login bug" in rendered
    assert "USER (subagent): sub task" in rendered
    assert "ASSISTANT (subagent): sub answer" in rendered


# --- cache (content-addressed) -------------------------------------------


def test_cache_hit_avoids_second_distill(root: Path, tmp_path: Path) -> None:
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    brain = DistillBrain()
    distill.distill_docs(root, "proj", [doc], brain)
    assert brain.distill_calls == 1
    # Re-run over the SAME raw + transcript: cache hit, no new brain call.
    distill.distill_docs(root, "proj", [doc], brain)
    assert brain.distill_calls == 1


def test_cache_invalidates_when_transcript_changes(root: Path, tmp_path: Path) -> None:
    """The Codex per-turn overwrite: same raw filename, grown transcript ⇒ the
    content fingerprint misses and re-distills (no stale digest)."""
    tp = tmp_path / "t.jsonl"
    _write_transcript(tp, _claude_events())
    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(tp))
    brain = DistillBrain()
    distill.distill_docs(root, "proj", [doc], brain)
    assert brain.distill_calls == 1

    _write_transcript(tp, _claude_events(text="a whole new turn", answer="new work in api.py"))
    distill.distill_docs(root, "proj", [doc], brain)
    assert brain.distill_calls == 2  # transcript changed ⇒ cache miss ⇒ re-distill


def test_cache_invalidates_when_redaction_policy_changes(root: Path, tmp_path: Path) -> None:
    """A new [redact].extra_patterns entry must invalidate the cache, so a digest
    redacted under the weaker old policy is never served stale (Codex review)."""
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    brain = DistillBrain()
    distill.distill_docs(root, "proj", [doc], brain, extra_patterns=())
    assert brain.distill_calls == 1
    # Same raw + transcript, but the user tightened redaction ⇒ cache miss.
    distill.distill_docs(root, "proj", [doc], brain, extra_patterns=("CUSTOM_SECRET",))
    assert brain.distill_calls == 2


def test_cache_version_is_part_of_fingerprint(root: Path, tmp_path: Path) -> None:
    """Bumping the cache version invalidates every cached digest (guards a
    redaction-table or render-format change)."""
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    fp1 = distill._source_fingerprint(doc.body, Path(str(t)), ())
    original = distill._CACHE_VERSION
    try:
        distill._CACHE_VERSION = original + 1
        fp2 = distill._source_fingerprint(doc.body, Path(str(t)), ())
    finally:
        distill._CACHE_VERSION = original
    assert fp1 != fp2


def test_dry_run_never_writes_cache(root: Path, tmp_path: Path) -> None:
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    brain = DistillBrain()
    distill.distill_docs(root, "proj", [doc], brain, write_cache=False)
    assert not (store.memory_dir("proj", root) / "raw" / ".digests").exists()


# --- integration: distill wired into curate() ----------------------------


def test_curate_distills_and_reports_counts(root: Path, tmp_path: Path) -> None:
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    _write_raw(root, "proj", "r1.md", body="thin", transcript_path=str(t))
    brain = DistillBrain()
    summary = engine.curate(root, "proj", brain)
    assert summary["distilled"] == 1
    assert summary["fallback"] == 0
    # the plan saw the digest, not the skim
    assert brain.distill_calls == 1


def test_curate_dry_run_reports_counts_and_writes_no_cache(root: Path, tmp_path: Path) -> None:
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    brain = DistillBrain()
    summary = engine.curate(root, "proj", brain, dry_run=True)
    assert summary["distilled"] == 1
    assert not (store.memory_dir("proj", root) / "raw" / ".digests").exists()


def test_digests_sidecar_is_invisible_to_list_raw(root: Path, tmp_path: Path) -> None:
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    engine.curate(root, "proj", DistillBrain())
    # A cache sidecar now exists but must not be enumerated as a raw.
    assert (store.memory_dir("proj", root) / "raw" / ".digests" / "r1.md").exists()
    raws = store.list_raw(root, "proj", unconsumed_only=False)
    assert [d.file_path.name for d in raws] == ["r1.md"]
