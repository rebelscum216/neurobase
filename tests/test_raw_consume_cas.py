"""The raw-capture consume race (ADR-0023 / Codex P1-DATA-INTEGRITY-002).

Scribes write raws from agent hooks, which must stay inside the ADR-0003 latency
budget and exit 0 — so a scribe can neither block on the project lock nor fail
when it is held. The race: a scribe overwrites a session-keyed raw while a
curate pass is mid-consume on it, and the pass writes its stale copy back with
``consumed: true``, destroying the newer capture.

Round 2 fix: the scribe takes the lock **non-blocking** and, when it loses,
writes a *fresh* filename instead of touching the contended path
(``core.lock.write_raw_guarded``). The digest guard on ``mark_consumed`` remains
only as defense-in-depth — it is explicitly **not** a compare-and-swap, because
its check and its write are separate syscalls (Codex forced a write between them
and the capture was lost). These tests pin both halves.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neurobase.core import lock, store
from neurobase.core.store_handle import StoreMode, open_store
from neurobase.curator import engine

PROJECT = "probe"
CAPTURED = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)


def _capture(
    root: Path,
    body: str,
    *,
    session_id: str = "sess1234",
    guarded: bool = False,
    captured_at: datetime = CAPTURED,
) -> Path:
    """A scribe capture at a fixed session-keyed path (the Codex per-turn
    overwrite is what makes the race reachable)."""
    store.ensure_tree(PROJECT, root)
    fields: dict[str, Any] = {
        "body": body,
        "agent": "codex",
        "session_id": session_id,
        "cwd": "/tmp",
        "branch": "main",
        "captured_at": captured_at,
    }
    if guarded:
        # The guarded path takes the caller's WRITE handle, so the capture still
        # goes through the ADR-0015 chokepoint (ADR-0023).
        return lock.write_raw_guarded(open_store(root, StoreMode.WRITE), PROJECT, **fields)
    return store.write_raw(root, PROJECT, **fields)


def _mem(root: Path) -> Path:
    """The project memory dir — what production passes to ``project_lock`` from
    its handle."""
    return store.memory_dir(PROJECT, root)


# --- the scribe side: never touch a raw the curator may be consuming -------


def test_guarded_capture_overwrites_when_uncontended(tmp_path: Path) -> None:
    """No pass in flight ⇒ the scribe wins the lock and the ordinary
    session-keyed overwrite happens (one raw, newest body)."""
    first = _capture(tmp_path, "body A", guarded=True)
    second = _capture(tmp_path, "body B", guarded=True)
    assert first == second
    raws = sorted((store.memory_dir(PROJECT, tmp_path) / "raw").glob("*.md"))
    assert len(raws) == 1
    assert "body B" in store.read_doc(first).body


def test_guarded_capture_writes_a_fresh_file_when_contended(tmp_path: Path) -> None:
    """A pass holds the lock ⇒ the scribe must NOT touch the raw being
    consumed. It writes a fresh filename and the original is untouched."""
    original = _capture(tmp_path, "body A")
    before = store.read_doc(original).body

    with lock.project_lock(_mem(tmp_path)):  # stand in for a curate pass
        fresh = _capture(tmp_path, "body B", guarded=True)

    assert fresh != original, "contended scribe overwrote the consuming path"
    assert "body B" in store.read_doc(fresh).body
    assert store.read_doc(original).body == before, "original raw was mutated"
    raws = sorted((store.memory_dir(PROJECT, tmp_path) / "raw").glob("*.md"))
    assert len(raws) == 2, "the capture must survive as its own raw"


def test_contended_captures_never_collide(tmp_path: Path) -> None:
    """Repeated contended captures each claim their own filename."""
    _capture(tmp_path, "body A")
    with lock.project_lock(_mem(tmp_path)):
        paths = {_capture(tmp_path, f"body {n}", guarded=True) for n in range(5)}
    assert len(paths) == 5


# --- the consume side: digest guard as defense-in-depth --------------------


def test_unchanged_raw_is_consumed(tmp_path: Path) -> None:
    path = _capture(tmp_path, "body A")
    doc = store.read_doc(path)
    assert store.mark_consumed(path, expect_digest=doc.digest) is not None
    assert store.read_doc(path).get("consumed") is True


def test_changed_raw_is_not_consumed(tmp_path: Path) -> None:
    path = _capture(tmp_path, "body A")
    planned = store.read_doc(path)
    _capture(tmp_path, "body B")  # unguarded write, simulating a lock bypass
    assert store.mark_consumed(path, expect_digest=planned.digest) is None
    after = store.read_doc(path)
    assert after.get("consumed") is False
    assert "body B" in after.body


def test_unguarded_consume_still_loses_the_capture(tmp_path: Path) -> None:
    """Teeth: without the digest guard the same interleaving marks B consumed.
    If this ever stops losing it, the guarded test above proves nothing."""
    path = _capture(tmp_path, "body A")
    store.read_doc(path)
    _capture(tmp_path, "body B")
    store.mark_consumed(path)  # the old, unguarded call
    assert store.read_doc(path).get("consumed") is True


def test_digest_changes_with_content(tmp_path: Path) -> None:
    path = _capture(tmp_path, "body A")
    first = store.read_doc(path).digest
    _capture(tmp_path, "body B")
    assert store.read_doc(path).digest != first


# --- the production path: drive the real curator (Codex P2-TEST-GAP-005) ---


class _OverwritingBrain:
    """A brain that performs a scribe capture *during* planning — the exact
    interleaving, injected at the point the curator is mid-pass."""

    name = "fake"

    def __init__(self, root: Path) -> None:
        self._root = root
        self.plan_calls = 0

    def plan_json(self, system: str, user: str) -> dict[str, Any]:
        self.plan_calls += 1
        # a scribe fires mid-plan, going through the real guarded path
        _capture(self._root, "body B — captured mid-plan", guarded=True)
        return {"upserts": [], "tombstones": []}

    def text(self, system: str, user: str) -> str:
        return "# Status\n\nsynth body"


def test_curator_pass_does_not_lose_a_mid_plan_capture(tmp_path: Path) -> None:
    """End-to-end through ``engine.curate``. Asserts the **mechanism-distinguishing**
    state, not just "B survived": the pass holds the lock, so the mid-plan scribe
    is contended and must land on a *separate* path, leaving A intact, consumed,
    and journaled. Every one of these fails if the lock is removed — see the
    control below (Codex P2-TEST-GAP-007)."""
    original = _capture(tmp_path, "body A")
    brain = _OverwritingBrain(tmp_path)

    engine.curate(tmp_path, PROJECT, brain)

    assert brain.plan_calls > 0, "the brain never ran; the test proves nothing"
    raws = sorted((store.memory_dir(PROJECT, tmp_path) / "raw").glob("*.md"))
    assert len(raws) == 2, "the contended capture must land on its own raw"

    a = store.read_doc(original)
    assert "body A" in a.body, "A was overwritten — the lock did not exclude the scribe"
    assert a.get("consumed") is True, "A was planned, so the pass must consume it"

    fresh = [p for p in raws if p != original]
    assert len(fresh) == 1
    b = store.read_doc(fresh[0])
    assert "body B — captured mid-plan" in b.body
    assert b.get("consumed") is False, "a capture the plan never saw was consumed"


def test_without_the_curator_lock_the_capture_is_lost(tmp_path: Path, monkeypatch: Any) -> None:
    """The control that gives the test above teeth. Neutralize ``project_lock``
    inside the engine and the guarded scribe wins an uncontended lock, overwrites
    A in place, and the pass loses A's content — exactly the outcome the wiring
    prevents. If this ever stops losing data, the assertions above are vacuous."""

    @contextmanager
    def _no_lock(*args: Any, **kwargs: Any) -> Any:
        yield None

    monkeypatch.setattr(engine.lock, "project_lock", _no_lock)

    original = _capture(tmp_path, "body A")
    engine.curate(tmp_path, PROJECT, _OverwritingBrain(tmp_path))

    raws = sorted((store.memory_dir(PROJECT, tmp_path) / "raw").glob("*.md"))
    assert len(raws) == 1, "without the lock the scribe overwrites in place"
    assert "body A" not in store.read_doc(original).body, "A survived; control is void"


def test_curator_journal_excludes_an_unconsumed_raw(tmp_path: Path) -> None:
    """The fold journal (ADR-0022) is the durable provenance artifact: A must be
    journaled as consumed, and the contended B must appear nowhere in it. The
    A-is-present assertion is what stops the loop going vacuous when the journal
    is empty."""
    original = _capture(tmp_path, "body A")
    brain = _OverwritingBrain(tmp_path)

    engine.curate(tmp_path, PROJECT, brain)

    log = store.memory_dir(PROJECT, tmp_path) / store.CURATOR_LOG
    assert log.exists(), "no fold journal was written"
    records = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    consumed = {
        entry["file"] for rec in records for entry in rec.get("fold", {}).get("consumed", [])
    }
    assert original.name in consumed, "the planned raw must be journaled as consumed"

    raw_dir = store.memory_dir(PROJECT, tmp_path) / "raw"
    fresh = [p for p in raw_dir.glob("*.md") if p != original]
    assert len(fresh) == 1
    assert fresh[0].name not in consumed, "an unconsumed raw leaked into the journal"
    for name in consumed:
        assert store.read_doc(raw_dir / name).get("consumed") is True


# --- P1-CORRECTNESS-006: fresh names keep spec §1 format + oldest-first -----


def test_contended_captures_keep_canonical_names_and_order(tmp_path: Path) -> None:
    """Two contended captures with **no pre-existing base file** must come back
    from ``list_raw`` oldest-first, and their *canonical* (suffix-stripped)
    portion must be a spec §1 ``{ts}_{agent}_{sid8}.md`` name. Each carries a
    ``__gNNNN`` suffix because the bare base is a holder's namespace
    (P1-DATA-INTEGRITY-001); the earlier ``-2``-suffix scheme failed both format
    and order (``-`` sorts before ``.``, so the second capture came back first)."""
    with lock.project_lock(_mem(tmp_path)):
        first = _capture(tmp_path, "first capture", guarded=True)
        second = _capture(tmp_path, "second capture", guarded=True)

    assert first != second
    for path in (first, second):
        assert re.search(r"__g\d{4,}(?=\.md$)", path.name), (
            f"{path.name} must be generation-suffixed (the base is reserved for a holder)"
        )
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.\d{6}Z_codex_[a-z0-9]+\.md",
            store.canonical_raw_name(path.name),
        ), f"{path.name} is not a canonical spec §1 raw filename once the suffix is stripped"

    docs = store.list_raw(tmp_path, PROJECT, unconsumed_only=True)
    bodies = [d.body for d in docs]
    assert "first capture" in bodies[0], "list_raw returned the captures out of order"
    assert "second capture" in bodies[1]


def test_contended_capture_filename_round_trips_from_its_frontmatter(tmp_path: Path) -> None:
    """Codex P1-CORRECTNESS-006: a raw's filename must be recomputable from its
    own stored ``captured_at``. The earlier fix advanced the filename timestamp
    while writing the caller's original value, so path and metadata disagreed."""
    with lock.project_lock(_mem(tmp_path)):
        first = _capture(tmp_path, "one", guarded=True)
        second = _capture(tmp_path, "two", guarded=True)

    assert first != second
    for path in (first, second):
        doc = store.read_doc(path)
        stamped = datetime.fromisoformat(str(doc.get("captured_at")).replace("Z", "+00:00"))
        # Contended captures carry a __gNNNN suffix (the base is a holder's), so the
        # spec §1 round-trip is on the canonical timestamp/key portion.
        assert store.raw_filename(stamped, "codex", "sess1234") == store.canonical_raw_name(
            path.name
        ), f"{path.name} does not round-trip from its frontmatter captured_at"


def test_contended_capture_from_a_long_running_session_sorts_last(tmp_path: Path) -> None:
    """True oldest-*capture*-first across the corpus, not just among colliding
    same-key calls. A Codex scribe passes its **session-start** time, so a
    capture made hours into a long session must not be filed back at that stale
    timestamp ahead of newer raws from other sessions."""
    session_start = datetime(2020, 1, 1, 8, 0, 0, tzinfo=UTC)
    intervening = datetime(2020, 6, 1, 11, 0, 0, tzinfo=UTC)

    old = _capture(tmp_path, "old session start", session_id="oldsess1", captured_at=session_start)
    mid = _capture(tmp_path, "intervening raw", session_id="othersess", captured_at=intervening)

    # the long-running session captures again, mid-pass, still keyed on its start
    with lock.project_lock(_mem(tmp_path)):
        late = _capture(
            tmp_path,
            "late capture from the old session",
            session_id="oldsess1",
            captured_at=session_start,
            guarded=True,
        )

    assert late not in (old, mid)
    order = [d.file_path for d in store.list_raw(tmp_path, PROJECT, unconsumed_only=True)]
    assert order == [old, mid, late], (
        "a contended capture was filed at its stale session-start timestamp and "
        f"sorted out of capture order: {[p.name for p in order]}"
    )


def _claim_raw(root: Path, captured_at: datetime, sid: str, body: str) -> Path:
    return store.write_raw(
        root,
        PROJECT,
        agent="codex",
        session_id=sid,
        cwd="/tmp",
        branch="main",
        captured_at=captured_at,
        body=body,
        unique=True,
    )


def test_list_raw_orders_by_capture_time_across_sessions(tmp_path: Path) -> None:
    """Codex P1-CORRECTNESS-006: three contended captures from session A at the
    same instant, then one genuinely later from session B. Order must be
    A0, A1, A2, B. The collisions are disambiguated by a generation suffix, not by
    advancing the timestamp, so all three A captures keep the *same* truthful
    captured_at and B (a later instant) still sorts last."""
    store.ensure_tree(PROJECT, tmp_path)
    same = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    later = datetime(2026, 7, 20, 12, 0, 1, tzinfo=UTC)

    # Every contended claim is suffixed (the bare base is a holder's namespace),
    # so the three same-instant A captures are __g0001, __g0002, __g0003 in claim
    # order — never the bare base (P1-DATA-INTEGRITY-001).
    a0 = _claim_raw(tmp_path, same, "aaaa1111", "A0")  # first claim → __g0001
    a1 = _claim_raw(tmp_path, same, "aaaa1111", "A1")  # collides → __g0002
    a2 = _claim_raw(tmp_path, same, "aaaa1111", "A2")  # collides → __g0003
    b = _claim_raw(tmp_path, later, "bbbb2222", "B genuinely later")

    order = [d.file_path for d in store.list_raw(tmp_path, PROJECT, unconsumed_only=True)]
    assert order == [a0, a1, a2, b], [p.name for p in order]
    # captured_at is never advanced: all three A generations share one instant
    a_times = {str(store.read_doc(p).get("captured_at")) for p in (a0, a1, a2)}
    assert len(a_times) == 1, f"a collision moved the capture time: {a_times}"
    # spec §1 round-trip MUST: the timestamp/key portion (generation stripped)
    # recomputes from each raw's own captured_at — including the generated names.
    for path in (a0, a1, a2, b):
        doc = store.read_doc(path)
        stamped = store.parse_captured_at(doc.get("captured_at"))
        assert stamped is not None
        assert store.canonical_raw_name(path.name) == store.raw_filename(
            stamped, "codex", doc.get("session_id")
        ), f"{path.name} timestamp portion does not round-trip"
    assert "__g0001" in a0.name and "__g0002" in a1.name and "__g0003" in a2.name, (
        "every contended claim must be generation-suffixed in claim order"
    )


def test_microsecond_collision_at_second_boundary_keeps_order(tmp_path: Path) -> None:
    """Codex round 6: the failing case. Two captures from session A at
    ``…00.999999`` (a collision), then a genuinely later B at ``…01.000000``.
    Advancing the timestamp by a microsecond rolled A's second capture to
    ``…01.000000`` and tied/inverted it with B (observed order A0, B, A1). With a
    generation suffix and untouched capture times, order is A0, A1, B."""
    store.ensure_tree(PROJECT, tmp_path)
    edge = datetime(2026, 7, 20, 12, 0, 0, 999999, tzinfo=UTC)
    rollover = datetime(2026, 7, 20, 12, 0, 1, 0, tzinfo=UTC)

    # Adversarial session keys (Codex's exact scenario): A sorts AFTER B by name,
    # so if A1's time is advanced to the rollover second it ties B and the
    # filename tiebreak wrongly puts B first (A0, B, A1). Order alone must catch it.
    a0 = _claim_raw(tmp_path, edge, "zzzz9999", "A0 at .999999")
    a1 = _claim_raw(tmp_path, edge, "zzzz9999", "A1 at .999999 (collision)")
    b = _claim_raw(tmp_path, rollover, "aaaa0000", "B genuinely later")

    order = [d.file_path for d in store.list_raw(tmp_path, PROJECT, unconsumed_only=True)]
    assert order == [a0, a1, b], [p.name for p in order]
    assert store.parse_captured_at(store.read_doc(a1).get("captured_at")) == edge, (
        "A1's capture time crossed the second boundary"
    )


def test_solo_contended_capture_is_still_suffixed_and_round_trips(tmp_path: Path) -> None:
    """Even with no collision at all, a contended (``unique=True``) claim carries
    ``__g0001`` — the bare base is reserved for the lock holder
    (P1-DATA-INTEGRITY-001). The *timestamp/key portion* still round-trips from
    the frontmatter (spec §1): ``canonical_raw_name`` strips the suffix and the
    remainder recomputes from ``captured_at``."""
    store.ensure_tree(PROJECT, tmp_path)
    path = _claim_raw(
        tmp_path, datetime(2026, 7, 20, 12, 0, 0, 123456, tzinfo=UTC), "aaaa1111", "solo"
    )
    doc = store.read_doc(path)
    stamped = store.parse_captured_at(doc.get("captured_at"))
    assert stamped is not None
    assert "__g0001" in path.name, f"a contended claim must reserve the base; got {path.name}"
    assert store.canonical_raw_name(path.name) == store.raw_filename(
        stamped, "codex", doc.get("session_id")
    )


def test_ordinary_holder_write_is_bare_and_round_trips(tmp_path: Path) -> None:
    """The lock-holder's ordinary (``unique=False``) write keeps the bare
    ``{ts}_{agent}_{sid8}.md`` name — no generation suffix — and round-trips
    whole from its frontmatter. This is the base namespace a contended claim must
    never touch (P1-DATA-INTEGRITY-001)."""
    path = _capture(
        tmp_path, "held", captured_at=datetime(2026, 7, 20, 12, 0, 0, 123456, tzinfo=UTC)
    )
    doc = store.read_doc(path)
    stamped = store.parse_captured_at(doc.get("captured_at"))
    assert stamped is not None
    assert "__g" not in path.name
    assert store.raw_filename(stamped, "codex", doc.get("session_id")) == path.name


def test_list_raw_orders_by_authoritative_captured_at_not_filename(tmp_path: Path) -> None:
    """``captured_at`` is authoritative. If a raw's frontmatter time disagrees
    with its filename — a hand-edit, or a future format migration — ``list_raw``
    follows the frontmatter, not the filename string. This is what makes the sort
    key robust and independent of the filename; sorting by name instead fails it."""
    raw_dir = store.memory_dir(PROJECT, tmp_path) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    # filename says :05 but the authoritative captured_at says :01 (earlier)
    x = raw_dir / "2026-07-20T12-00-05.000000Z_codex_xxxx1111.md"
    store.write_doc(
        x,
        {
            "agent": "codex",
            "session_id": "xxxx1111",
            "captured_at": "2026-07-20T12:00:01Z",
            "consumed": False,
        },
        "X",
    )
    y = raw_dir / "2026-07-20T12-00-02.000000Z_codex_yyyy2222.md"
    store.write_doc(
        y,
        {
            "agent": "codex",
            "session_id": "yyyy2222",
            "captured_at": "2026-07-20T12:00:02Z",
            "consumed": False,
        },
        "Y",
    )

    order = [d.file_path.name for d in store.list_raw(tmp_path, PROJECT)]
    assert order == [x.name, y.name], "list_raw sorted by filename, not captured_at"


def test_generation_tiebreak_is_numeric_past_four_digits(tmp_path: Path) -> None:
    """P2-CORRECTNESS-001: within one instant/key, ``__g10000`` must sort *after*
    ``__g9999``. A lexical filename tiebreak inverts them (``'1' < '9'``), so this
    pins the numeric generation sort — without constructing ten thousand captures.
    Two raws are written out of order under one ``captured_at`` and ``list_raw``
    must return them in generation order."""
    raw_dir = store.memory_dir(PROJECT, tmp_path) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    ts, captured = "2026-07-20T12-00-00.000000Z", "2026-07-20T12:00:00Z"
    g9999 = raw_dir / f"{ts}_codex_aaaa1111__g9999.md"
    g10000 = raw_dir / f"{ts}_codex_aaaa1111__g10000.md"
    frontmatter = {
        "agent": "codex",
        "session_id": "aaaa1111",
        "captured_at": captured,
        "consumed": False,
    }
    # write the higher generation first so a stable sort cannot fake the order
    store.write_doc(g10000, frontmatter, "gen 10000")
    store.write_doc(g9999, frontmatter, "gen 9999")

    order = [d.file_path for d in store.list_raw(tmp_path, PROJECT)]
    assert order == [g9999, g10000], [p.name for p in order]


def test_contended_capture_claims_a_fresh_name_on_a_same_instant_collision(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """``write_raw_guarded``'s contended branch passes ``unique=True``, and this
    is the ONLY test that pins it.

    Found by mutation while re-deriving this branch: dropping ``unique=True``
    left the whole inherited suite green (including the review-approved
    contended-capture tests). The reason is that the contended branch also
    restamps ``captured_at=now()``, and at microsecond resolution that alone
    produces an unused filename — so the ``unique`` flag never did any work in
    any existing test. It only bites when two contended captures land in the
    *same microsecond* under the same agent/session key, which nothing
    constructed.

    So freeze ``now`` and fire two contended captures into one instant. With
    ``unique=True`` they claim ``__g0001`` then ``__g0002`` and both survive;
    without it, ``write_raw`` resolves to the same session-keyed path and the
    second silently overwrites the first — losing a capture, which is the entire
    bug this module exists to prevent. (Neither ever claims the bare base — that
    is reserved for a lock holder; see the P1-DATA-INTEGRITY-001 regression
    below.)
    """

    class _FrozenClock:
        @staticmethod
        def now(tz: Any = None) -> datetime:
            return datetime(2026, 7, 20, 15, 30, 0, 500000, tzinfo=UTC)

    monkeypatch.setattr(lock, "datetime", _FrozenClock)

    with lock.project_lock(_mem(tmp_path)):
        first = _capture(tmp_path, "contended one", guarded=True)
        second = _capture(tmp_path, "contended two", guarded=True)

    assert first != second, "the second contended capture overwrote the first"
    assert "__g0001" in first.name, f"expected a generation suffix, got {first.name}"
    assert "__g0002" in second.name, f"expected a generation suffix, got {second.name}"
    assert "contended one" in store.read_doc(first).body
    assert "contended two" in store.read_doc(second).body

    # Both are real, orderable raws: same truthful instant, generation as tiebreak.
    order = [d.file_path for d in store.list_raw(tmp_path, PROJECT, unconsumed_only=True)]
    assert order == [first, second], [p.name for p in order]
    times = {str(store.read_doc(p).get("captured_at")) for p in (first, second)}
    assert len(times) == 1, f"the collision moved a capture time: {times}"


def _holder_write(root: Path, sid: str, captured_at: datetime, body: str) -> Path:
    """The lock HOLDER's ordinary, session-keyed write (``unique=False``): it
    lands on the bare ``{ts}_{agent}_{sid8}.md`` base and atomically replaces it."""
    return store.write_raw(
        root,
        PROJECT,
        agent="codex",
        session_id=sid,
        cwd="/tmp",
        branch="main",
        captured_at=captured_at,
        body=body,
        unique=False,
    )


def _loser_write(root: Path, sid: str, captured_at: datetime, body: str) -> Path:
    """The lock LOSER's contended write (``unique=True``) — what
    ``write_raw_guarded`` does when it cannot take the lock."""
    return store.write_raw(
        root,
        PROJECT,
        agent="codex",
        session_id=sid,
        cwd="/tmp",
        branch="main",
        captured_at=captured_at,
        body=body,
        unique=True,
    )


def test_contended_loser_and_lock_holder_never_lose_a_capture_either_order(
    tmp_path: Path,
) -> None:
    """P1-DATA-INTEGRITY-001 regression. The bug lived between the lock *holder*
    and a lock *loser* at the **same instant under the same agent/session key** —
    the interleaving the frozen-clock test above could not construct, because it
    only ever raced two losers, never a loser against the ordinary base write.

    The holder does the ordinary session-keyed write to the bare base; the loser
    writes ``unique=True``. Before the fix, ``unique`` claimed the bare base
    whenever it was still *absent* — so a loser that ran first ``O_EXCL``-claimed
    the exact base the holder was *about to* ``os.replace`` onto, and the holder's
    atomic write then destroyed the loser's capture. The fix reserves the base for
    the holder: a loser always takes ``__gNNNN``, so the two names are disjoint in
    **either** interleaving. Both orderings must leave two distinct raws with both
    bodies intact.

    No threads: the interleaving is expressed as a strict call order at one frozen
    instant. "Loser first, then holder" is the ordering that lost data; running the
    loser's whole write before the holder's is exactly "the base is still absent
    when the loser claims" — the pause Codex asked the regression to model.
    """
    instant = datetime(2026, 7, 20, 15, 30, 0, 500000, tzinfo=UTC)

    def surviving(root: Path) -> set[str]:
        return {p.name for p in (store.memory_dir(PROJECT, root) / "raw").glob("*.md")}

    # --- Ordering 1: loser claims FIRST, then the holder writes the base --------
    # (the data-losing interleaving; base absent when the loser claims)
    a_root = tmp_path / "loser_first"
    store.ensure_tree(PROJECT, a_root)
    loser = _loser_write(a_root, "sess1234", instant, "loser capture")
    holder = _holder_write(a_root, "sess1234", instant, "holder capture")

    assert loser != holder, "the loser claimed the holder's base — its capture will be clobbered"
    assert "__g" not in holder.name, "the holder must own the bare base"
    assert "__g0001" in loser.name, "the loser must be pushed off the base onto a suffix"
    assert len(surviving(a_root)) == 2, "a capture was lost when the holder overwrote the base"
    assert "loser capture" in store.read_doc(loser).body
    assert "holder capture" in store.read_doc(holder).body

    # --- Ordering 2: holder writes the base FIRST, then the loser claims --------
    # (already safe pre-fix; must stay safe — no regression the other way)
    b_root = tmp_path / "holder_first"
    store.ensure_tree(PROJECT, b_root)
    holder2 = _holder_write(b_root, "sess1234", instant, "holder capture")
    loser2 = _loser_write(b_root, "sess1234", instant, "loser capture")

    assert loser2 != holder2
    assert "__g" not in holder2.name and "__g0001" in loser2.name
    assert len(surviving(b_root)) == 2, "the later contended claim collided with the base"
    assert "holder capture" in store.read_doc(holder2).body
    assert "loser capture" in store.read_doc(loser2).body
