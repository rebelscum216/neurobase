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
    assert counts["distilled"] == 1
    assert counts["fallback"] == 0
    assert counts["truncated"] == 0  # G11: a short digest is not trimmed
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
    assert counts["distilled"] == 0
    assert counts["fallback"] == 0
    assert brain.distill_calls == 0
    assert out[0].body == "skim"


def test_v1_raw_without_transcript_path_falls_back(root: Path) -> None:
    doc = _write_raw(root, "proj", "r1.md", body="skim")  # no transcript_path
    brain = DistillBrain()
    out, counts = distill.distill_docs(root, "proj", [doc], brain)
    assert counts["distilled"] == 0
    assert counts["fallback"] == 1
    assert counts["fallback_no_pointer"] == 1  # the reason is now reported
    assert brain.distill_calls == 0
    assert out[0].body == "skim"


def test_missing_transcript_falls_back(root: Path) -> None:
    doc = _write_raw(root, "proj", "r1.md", body="skim", transcript_path="/nope/gone.jsonl")
    brain = DistillBrain()
    out, counts = distill.distill_docs(root, "proj", [doc], brain)
    assert counts["distilled"] == 0
    assert counts["fallback"] == 1
    assert counts["fallback_transcript_gone"] == 1  # the reason is now reported
    assert brain.distill_calls == 0
    assert out[0].body == "skim"


def test_unsupported_schema_store_falls_back_without_cache_access(
    root: Path, tmp_path: Path
) -> None:
    """§10 chokepoint + D16: ``_digests_dir`` self-opens a validated handle, so a
    store whose ``store.toml`` schema is newer than this binary supports refuses
    the digest cache and the raw degrades to its skim — no brain call, no cache
    write, no aborted pass. This pins the guard ``_digests_dir`` gained in 4b:
    with the pre-4b raw-root ``store.memory_dir`` the schema was unchecked and this
    raw would have distilled normally (``UnsupportedSchemaError`` from the self-open
    is neither ``BrainError`` nor ``BudgetExhausted``, so it lands on
    ``_distill_one``'s terminal skim path, not a pass-level breaker)."""
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", body="skim", transcript_path=str(t))
    # A schema newer than the binary supports: open_store(READ) refuses it.
    (root / "store.toml").write_text(
        f'schema = {store.STORE_SCHEMA_VERSION + 1}\ncreated_at = "2020-01-01T00:00:00Z"\n',
        encoding="utf-8",
    )
    brain = DistillBrain()
    out, counts = distill.distill_docs(root, "proj", [doc], brain)
    assert counts["distilled"] == 0
    assert counts["fallback"] == 1
    assert counts["fallback_error"] == 1  # the reason is now reported
    assert brain.distill_calls == 0
    assert out[0].body == "skim"
    # The refusal happens before any cache access, so no digest sidecar is written.
    assert not (store.memory_dir("proj", root) / "raw" / distill._DIGESTS_DIRNAME).exists()


def test_brain_error_falls_back(root: Path, tmp_path: Path) -> None:
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", body="skim", transcript_path=str(t))
    brain = DistillBrain(digest=BrainError("distill blew up"))
    out, counts = distill.distill_docs(root, "proj", [doc], brain)
    assert counts["distilled"] == 0
    assert counts["fallback"] == 1
    assert out[0].body == "skim"


def test_brain_error_stops_distilling_remaining_raws(root: Path, tmp_path: Path) -> None:
    docs = []
    for index in range(3):
        transcript = _write_transcript(tmp_path / f"t{index}.jsonl", _claude_events())
        docs.append(
            _write_raw(
                root,
                "proj",
                f"r{index}.md",
                body=f"skim {index}",
                transcript_path=str(transcript),
            )
        )
    brain = DistillBrain(digest=BrainError("backend unavailable"))

    out, counts = distill.distill_docs(root, "proj", docs, brain)

    assert brain.distill_calls == 1
    assert counts["distilled"] == 0
    assert counts["fallback"] == 3
    # A systemic brain error trips the pass breaker, so raws after the first
    # are never attempted — counted separately from ones that were tried.
    assert counts["fallback_not_attempted"] == 3
    assert [doc.body for doc in out] == ["skim 0", "skim 1", "skim 2"]


def test_refusal_shaped_output_falls_back(root: Path, tmp_path: Path) -> None:
    """The S-cf5 role-hijack: a conversational reply with no expected heading is
    a distill failure ⇒ skim (D16/F3)."""
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", body="skim", transcript_path=str(t))
    brain = DistillBrain(digest="I'm going to stop — what do you want instead? 1/2/3")
    out, counts = distill.distill_docs(root, "proj", [doc], brain)
    assert counts["distilled"] == 0
    assert counts["fallback"] == 1
    assert counts["fallback_invalid_digest"] == 1  # the reason is now reported
    assert out[0].body == "skim"


def test_codex_transcript_falls_back_deferred_renderer(root: Path, tmp_path: Path) -> None:
    """Codex render is deferred (ADR-0013 S-cf3); a Codex raw degrades to skim
    without a brain call."""
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", body="skim", agent="codex", transcript_path=str(t))
    brain = DistillBrain()
    out, counts = distill.distill_docs(root, "proj", [doc], brain)
    assert counts["distilled"] == 0
    assert counts["fallback"] == 1
    assert counts["fallback_unsupported_agent"] == 1  # the reason is now reported
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


# --- G11: bounding trims section BODIES, never deletes sections -----------
#
# The pre-G11 `_bound` kept a prefix, so it always cut the tail. `DISTILL_SYSTEM`
# prescribes a fixed heading order ending in `## Unresolved`, so that
# deterministically deleted the open-threads section from every dense session
# (measured 2026-08-04: 15 of 17 capped digests in the live store had no
# `## Unresolved`, against 9 of 9 under the cap). These tests pin the invariant
# that replaced it, and the first one is the regression guard: reverting to a
# prefix cut fails it.

_SECTIONS = ("## Decisions", "## Discoveries & gotchas", "## State changes", "## Unresolved")


def _oversize_digest(*, tail: str = "- ship the migration before Friday.") -> str:
    """Four sections, verbose early ones, a short but load-bearing last one.
    Comfortably over the cap so bounding must do something."""
    filler = "\n".join(f"- decision {i}: chose A over B because C." for i in range(120))
    findings = "\n".join(f"- finding {i}: root cause was D, not E." for i in range(120))
    return (
        f"## Decisions\n{filler}\n\n"
        f"## Discoveries & gotchas\n{findings}\n\n"
        f"## State changes\n- edited auth.py; 12 tests passed.\n\n"
        f"## Unresolved\n{tail}"
    )


def test_bounding_never_deletes_a_later_section() -> None:
    """THE regression guard (review finding P2-DOCS-PLAN-ACCURACY-007).

    A bounded digest that keeps an early heading while dropping a later one is
    exactly the G11 defect, and it satisfies the numeric F1 contract *and*
    `_is_valid_digest` — which accepts any ONE expected heading. So neither of
    those checks can catch it; this test is what does.
    """
    digest = _oversize_digest()
    assert len(digest) > distill.DIGEST_MAX_CHARS  # precondition

    bounded, truncated = distill._bound(digest)

    assert truncated is True
    assert len(bounded) <= distill.DIGEST_MAX_CHARS  # (a)
    for heading in _SECTIONS:  # (e) every section present on input survives
        assert heading in bounded, f"{heading} was deleted by bounding"
    # …and the last section keeps its actual content, not just its heading (f).
    assert "ship the migration before Friday" in bounded
    # The old prefix cut is what this replaced: prove it would have failed here.
    assert "## Unresolved" not in distill._prefix_cut(digest)


def test_bounding_preserves_section_order() -> None:
    """(e) also covers ORDER: a reader (and the fold) sees the prescribed
    sequence, so bounding must not reshuffle while redistributing space."""
    bounded, _ = distill._bound(_oversize_digest())
    positions = [bounded.index(h) for h in _SECTIONS]
    assert positions == sorted(positions)


def test_bounding_does_not_fabricate_an_absent_section() -> None:
    """The counterpart to the guard above, and the reason it is stated as
    *preservation* rather than "all four headings present": `DISTILL_SYSTEM`
    explicitly permits omitting a section that has no content, so a digest that
    legitimately arrived without `## Unresolved` must not gain one."""
    filler = "\n".join(f"- decision {i}: chose A over B because C." for i in range(200))
    digest = f"## Decisions\n{filler}\n\n## State changes\n- edited auth.py."
    assert len(digest) > distill.DIGEST_MAX_CHARS

    bounded, truncated = distill._bound(digest)

    assert truncated is True
    assert "## Unresolved" not in bounded
    assert "## Decisions" in bounded and "## State changes" in bounded


def test_trimming_falls_on_the_longest_sections_not_the_last() -> None:
    """The mechanism behind (e): water-fill to a common level, so a short section
    survives whole and only verbose ones lose text. Position must not matter."""
    long_body = "\n".join(f"- decision {i}: a fairly wordy justification." for i in range(200))
    short_tail = "- one open thread, stated briefly."
    digest = f"## Decisions\n{long_body}\n\n## Unresolved\n{short_tail}"

    bounded, _ = distill._bound(digest)

    assert short_tail in bounded  # the short LAST section is untouched
    assert distill._SECTION_TRIM_MARKER in bounded  # something was trimmed…
    unresolved = bounded[bounded.index("## Unresolved") :]
    assert distill._SECTION_TRIM_MARKER not in unresolved  # …and it wasn't this


def test_marker_is_present_only_when_something_was_trimmed() -> None:
    """(b). The marker is also how a reader (and `truncated`) knows, so a digest
    that fits must not carry it — otherwise every digest looks lossy."""
    small = "## Decisions\n- one short decision."
    bounded, truncated = distill._bound(small)
    assert bounded == small
    assert truncated is False
    assert distill.DIGEST_TRUNC_MARKER.strip() not in bounded

    bounded, truncated = distill._bound(_oversize_digest())
    assert truncated is True
    assert bounded.endswith(distill.DIGEST_TRUNC_MARKER)


def test_bounding_cannot_invalidate_an_already_valid_digest() -> None:
    """(c). `_is_valid_digest` runs BEFORE `_bound` in `_distill_one`, so a digest
    that passed the shape check must still pass it afterwards — otherwise
    bounding could turn a usable digest into one the pass rejects."""
    digest = _oversize_digest()
    assert distill._is_valid_digest(digest)
    bounded, _ = distill._bound(digest)
    assert distill._is_valid_digest(bounded)


def test_unparseable_shape_falls_back_to_the_prefix_cut() -> None:
    """(d). No recognized headings means nothing to preserve, so the original
    behaviour is correct — but the cap still binds."""
    junk = "x" * (distill.DIGEST_MAX_CHARS + 2000)
    bounded, truncated = distill._bound(junk)
    assert truncated is True
    assert len(bounded) <= distill.DIGEST_MAX_CHARS
    assert bounded.endswith(distill.DIGEST_TRUNC_MARKER)


def test_every_surviving_section_keeps_a_content_floor() -> None:
    """(f). Reducing a section to a bare heading satisfies "the heading is still
    there" while losing everything the section was for, so the floor is part of
    the invariant rather than a nicety."""
    bodies = "\n".join(f"- item {i}: some detail here." for i in range(150))
    digest = "\n\n".join(f"{h}\n{bodies}" for h in _SECTIONS)

    bounded, _ = distill._bound(digest)

    for heading in _SECTIONS:
        start = bounded.index(heading) + len(heading)
        nxt = [bounded.index(h) for h in _SECTIONS if bounded.index(h) > start]
        body = bounded[start : min(nxt)] if nxt else bounded[start:]
        body = body.replace(distill._SECTION_TRIM_MARKER, "").replace(
            distill.DIGEST_TRUNC_MARKER, ""
        )
        assert len(body.strip()) >= distill.DIGEST_SECTION_FLOOR_CHARS, f"{heading} floored out"


def test_too_many_sections_for_the_cap_falls_back_rather_than_overrunning() -> None:
    """When (e)+(f) cannot both hold, (a) wins — the cap is the contract and the
    section invariant is the improvement. A merge can repeat headings, so this is
    reachable rather than theoretical."""
    # 60 sections need 60 × (floor + markers) well beyond the cap, and the digest
    # itself must exceed the cap to reach the bounding path at all.
    block = "- a block body long enough that sixty of them overrun the cap." * 3
    digest = "\n\n".join(f"## Decisions\n{block}" for i in range(60))
    assert len(digest) > distill.DIGEST_MAX_CHARS  # precondition

    bounded, truncated = distill._bound(digest)

    assert truncated is True
    assert len(bounded) <= distill.DIGEST_MAX_CHARS
    assert bounded.endswith(distill.DIGEST_TRUNC_MARKER)


def test_chunk_drop_prefix_survives_bounding() -> None:
    """`_distill_one` prepends `[distill: N middle chunk(s) dropped for size]`
    when the INPUT was truncated. That is a different loss from this one and it
    must not be the thing bounding discards, or a digest can silently lose the
    only record that its source was incomplete."""
    prefix = "[distill: 2 middle chunk(s) dropped for size]"
    bounded, _ = distill._bound(f"{prefix}\n\n{_oversize_digest()}")
    assert bounded.startswith(prefix)
    assert "## Unresolved" in bounded


def test_truncation_is_counted(root: Path, tmp_path: Path) -> None:
    """G11 was invisible in aggregate: the only signal was an in-body marker, so
    "how often are we discarding the tail" needed a body grep."""
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    brain = DistillBrain(digest=_oversize_digest())

    out, counts = distill.distill_docs(root, "proj", [doc], brain)

    assert counts["distilled"] == 1
    assert counts["truncated"] == 1
    assert "## Unresolved" in out[0].body


def test_fallback_is_decomposed_by_cause(root: Path, tmp_path: Path) -> None:
    """A single `fallback` count conflated four unrelated situations, which made
    "is the pipeline healthy?" unanswerable: a raw with no transcript pointer, a
    transcript the harness deleted (G10's silent loss), an agent with no verified
    renderer (every Codex capture, permanently), and a real failure."""
    t = _write_transcript(tmp_path / "t.jsonl", _claude_events())
    docs = [
        _write_raw(root, "proj", "r1.md"),  # v1: no pointer
        _write_raw(root, "proj", "r2.md", transcript_path=str(tmp_path / "gone.jsonl")),
        _write_raw(root, "proj", "r3.md", agent="codex", transcript_path=str(t)),
        _write_raw(root, "proj", "r4.md", transcript_path=str(t)),  # distills
    ]

    _, counts = distill.distill_docs(root, "proj", docs, DistillBrain())

    assert counts["distilled"] == 1
    assert counts["fallback"] == 3  # the total still means what it meant
    assert counts["fallback_no_pointer"] == 1
    assert counts["fallback_transcript_gone"] == 1  # G10, now visible
    assert counts["fallback_unsupported_agent"] == 1  # Codex, permanent not transient
    assert counts["fallback_error"] == 0


def test_detail_rides_the_journal_and_not_the_printed_summary(root: Path, tmp_path: Path) -> None:
    """The summary's key set is pinned (spec §2.0 D16 names `distilled`/`fallback`;
    `test_summary_key_set_is_exact_and_carries_no_fold` fails loudly on drift), so
    the breakdown goes where per-raw detail already lives — the journal record,
    the same split `fold` uses. Growing the printed summary would be a deliberate
    contract change, not a side effect of adding observability."""
    _write_raw(root, "proj", "r1.md")  # no pointer ⇒ a fallback with a reason
    summary = engine.curate(root, "proj", DistillBrain())

    assert not [k for k in summary if k.startswith("fallback_") or k == "truncated"]
    assert summary["fallback"] == 1

    log = store.memory_dir("proj", root) / engine.CURATOR_LOG
    record = [json.loads(line) for line in log.read_text().splitlines()][-1]
    assert record["distill_detail"]["fallback_no_pointer"] == 1
    assert "distill_detail" not in summary


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
    scrubbed by ``_tool_use_line``'s command-channel redactor (``redact_command``,
    D17) — a *different* redactor than the tool_result/prose path.

    The fixture is chosen so this test actually pins ``scrub_command`` and isn't
    self-satisfied by the whole-render ``redact()`` defense-in-depth pass in
    ``_distill_one`` (Codex round-1 F1): ``pytest --api-key=<val>`` is a
    command-flag shape that ``redact_command`` catches but plain ``redact`` does
    *not* once the value sits behind the ``command=`` label. So if the command
    branch regressed to plain ``scrub`` or no scrub, the secret would survive and
    this test would fail."""
    secret_value = "supersecretvalue123"
    command = f"pytest --api-key={secret_value}"
    events = [
        {"type": "user", "message": {"role": "user", "content": "run the tests"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {"command": command},
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
    # The command line was rendered (proves _tool_use_line ran)...
    assert "[tool_use Bash] command=" in rendered
    # ...the command-channel scrubber fired on it (redaction marker present)...
    assert "[REDACTED:" in rendered
    # ...and the secret never reached the brain.
    assert secret_value not in rendered
    # nor the cached digest sidecar
    cache = store.memory_dir("proj", root) / "raw" / ".digests" / "r1.md"
    assert secret_value not in cache.read_text(encoding="utf-8")


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
    """When the render exceeds the chunk cap, the **middle** chunks are dropped
    while the head and tail are kept, and the digest is prefixed with a visible
    drop marker so the loss is never silent (``_chunk`` + the ``dropped`` path in
    ``_distill_one``).

    To pin the head+tail *retention* (not just the marker — Codex round-1 F2), the
    render carries distinguishable HEAD/MIDDLE/TAIL sentinels and a capturing brain
    records the per-chunk prompts actually sent: HEAD and TAIL must appear in them,
    MIDDLE must not."""
    # 6-char "USER: " prefix + sentinels spaced so HEAD lands in the first chunk,
    # TAIL in the last, and MIDDLE deep in the dropped interior. At chunk_chars=50
    # / MAX_DISTILL_CHUNKS=5, _chunk keeps the first 3 and last 2 chunks.
    text = "HEADsentinel" + ("a" * 300) + "MIDDLEsentinel" + ("a" * 300) + "TAILsentinel"
    events = [
        {"type": "user", "message": {"role": "user", "content": text}},
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        },
    ]
    t = _write_transcript(tmp_path / "t.jsonl", events)

    chunk_prompts: list[str] = []

    class CapturingBrain(DistillBrain):
        def text(self, system: str, user: str) -> str:
            if system.startswith("You compress"):  # per-chunk calls only, not merge
                chunk_prompts.append(user)
            return super().text(system, user)

    doc = _write_raw(root, "proj", "r1.md", transcript_path=str(t))
    # chunk_chars small ⇒ the render splits into far more than MAX_DISTILL_CHUNKS
    # (5) chunks, forcing the middle-drop.
    out, counts = distill.distill_docs(root, "proj", [doc], CapturingBrain(), chunk_chars=50)

    assert counts["distilled"] == 1
    # The drop is marked in the digest body...
    assert "middle chunk(s) dropped for size" in out[0].body
    # ...and only 5 chunks (MAX_DISTILL_CHUNKS) were actually sent to the brain...
    assert len(chunk_prompts) == 5
    # ...carrying the head and tail, but not the dropped middle.
    sent = "\n".join(chunk_prompts)
    assert "HEADsentinel" in sent
    assert "TAILsentinel" in sent
    assert "MIDDLEsentinel" not in sent


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
