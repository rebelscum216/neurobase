"""Doctor's `model mixture` line, read from records the **real engine** wrote.

Same standard as `test_curate_health_end_to_end.py` (round 3, F3): the fake brain
carries a real `BrainUsage` and folds real-shaped CLI envelopes through
`BrainUsage.record`, so the per-model counting rule under test is the producer's
own, not a fixture's idea of it. A handcrafted `brain_usage` dict would let
producer and consumer drift apart with every test still green.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from neurobase.brain.base import BrainError, BrainUsage
from neurobase.cli import diagnostics
from neurobase.core import projects, store
from neurobase.curator import engine

#: The two ids a single `claude -p` call reports: the main model plus the
#: auxiliary. Both on every call is the HEALTHY case — the check must not read
#: "two models" as a fault.
MAIN = "claude-opus-4-6"
AUX = "claude-haiku-4-5"

PLAN = {"upserts": [{"slug": "f", "body": "b", "from_raw": ["r1.md"]}], "tombstones": []}


class _Brain:
    """A brain that reports usage the way the Claude CLI backend does.

    `per_call_models` supplies the `modelUsage` keys for call 1, call 2, ...; the
    last entry repeats if the pass makes more calls than were scripted.
    """

    name = "fake"

    def __init__(self, per_call_models: list[list[str] | None]) -> None:
        self._per_call = per_call_models
        self._calls = 0
        self.usage = BrainUsage()

    def _record(self) -> None:
        index = min(self._calls, len(self._per_call) - 1)
        models = self._per_call[index]
        self._calls += 1
        envelope: dict[str, Any] = {"usage": {"input_tokens": 10, "output_tokens": 5}}
        if models is not None:
            envelope["modelUsage"] = {model: {"inputTokens": 10} for model in models}
        self.usage.record(envelope)

    def plan_json(self, system: str, user: str) -> dict:
        self._record()
        return PLAN

    def text(self, system: str, user: str) -> str:
        self._record()
        return "# Status\n\nbody"


@pytest.fixture()
def project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    repo.mkdir()
    slug = projects.register_project(root, repo)
    store.ensure_tree(slug, root)
    assert slug == "repo"
    return root, repo


def _write_raw(root: Path, name: str, *, when: str = "2026-08-05T12:00:00Z") -> None:
    store.write_doc(
        store.memory_dir("repo", root) / "raw" / name,
        {
            "agent": "claude",
            "session_id": "s1",
            "cwd": "/x",
            "branch": "main",
            "captured_at": when,
            "consumed": False,
            "capture_version": 2,
        },
        "raw body",
    )


def _journal(root: Path) -> list[dict]:
    path = store.memory_dir("repo", root) / store.CURATOR_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _mixture(root: Path, repo: Path) -> diagnostics.Check:
    checks = diagnostics._model_mixture_check(root, repo)
    assert len(checks) == 1, f"expected exactly one check, got {checks}"
    return checks[0]


# --- the case the naive version got wrong -----------------------------------


def test_two_models_on_every_call_is_healthy_not_a_warning(project: tuple[Path, Path]) -> None:
    """A `claude -p` call reports the main model AND an auxiliary. If this reads
    as a fault, the check fires on every healthy pass and is worse than nothing.
    """
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN, AUX]]))

    usage = _journal(root)[-1]["brain_usage"]
    assert usage["calls"] > 1, "the pass must make several calls or this proves nothing"
    assert usage["models"] == {MAIN: usage["calls"], AUX: usage["calls"]}

    check = _mixture(root, repo)
    assert check.status == "ok"
    assert MAIN in check.detail and AUX in check.detail


#: Words that assert an explanation the journal cannot supply. Round 1 shipped
#: "fallback"/"capacity"; round 2 caught the subtler ones — a *change* the record
#: does not prove, missing *coverage* it equally does not prove, and a claim about
#: *digests* when the counts aggregate every stage of the pass.
UNSUPPORTED_CLAIMS = ("fallback", "capacity", "changed", "does not cover", "digest")


def _assert_claims_nothing_it_cannot_prove(check: diagnostics.Check) -> None:
    text = f"{check.detail} {check.remedy or ''}".lower()
    for word in UNSUPPORTED_CLAIMS:
        assert word not in text, f"{word!r} asserts an explanation the journal cannot supply"


@pytest.mark.parametrize(
    ("per_call", "shape"),
    [
        # Fully observed A-then-B: every envelope reported, the set really did
        # change — but no model reaches `calls`, so nothing in the record says so.
        ([[MAIN], [AUX]], "a genuine switch"),
        # One model throughout; the second envelope simply reported no modelUsage.
        # Identical in the journal to the row above.
        ([[MAIN], None], "incomplete telemetry"),
        # A covering model plus a partial one. Round 2 showed even this cannot
        # prove a change: `record` stringifies model keys, so two distinct keys can
        # collide and reach `count == calls` without full envelope coverage.
        ([[MAIN, AUX], [MAIN]], "a partial beside a covering model"),
    ],
)
def test_every_uncovered_shape_is_reported_the_SAME_way_and_explains_nothing(
    project: tuple[Path, Path], per_call: list[list[str] | None], shape: str
) -> None:
    """Round 2, P2-CORRECTNESS-001 + P2-OBSERVABILITY-005 — and the reason the
    proof mechanism was deleted rather than narrowed.

    These three shapes have genuinely different causes and are **indistinguishable
    in the journal**. Any check that reports them differently is claiming
    something the record does not carry, so all three must produce one neutral
    line: the counts, and no explanation.
    """
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain(per_call))

    usage = _journal(root)[-1]["brain_usage"]
    calls = usage["calls"]
    assert any(0 < n < calls for n in usage["models"].values()), (
        f"{shape}: the uncovered shape must actually be produced, or this proves nothing"
    )

    check = _mixture(root, repo)
    assert check.status == "warn"
    assert "did not all cover the pass" in check.detail
    assert f"of {calls}" in check.detail
    assert check.remedy is not None and "cannot say why" in check.remedy.lower()
    _assert_claims_nothing_it_cannot_prove(check)


def test_the_remedy_does_not_pin_the_mixture_on_digests(project: tuple[Path, Path]) -> None:
    """Round 2, P2-OBSERVABILITY-005. `models` aggregates every brain envelope in
    the pass and is not attributed per stage. In this very fixture distillation
    falls back deterministically, so the two model observations come from planning
    and synthesis and **no digest is brain-produced at all** — yet the round-1
    remedy told the reader that pass's digests came from different model sets."""
    root, repo = project
    _write_raw(root, "r1.md")
    brain = _Brain([[MAIN, AUX], [MAIN]])
    engine.curate(root, "repo", brain)

    assert _journal(root)[-1]["brain_usage"]["calls"] == 2, (
        "exactly plan + synthesis; a third call would mean distill used the brain "
        "and the no-digest premise of this test would be false"
    )

    check = _mixture(root, repo)
    assert check.remedy is not None
    assert "digest" not in check.remedy.lower()
    assert "aggregate" in check.remedy.lower() and "per stage" in check.remedy.lower()


def test_a_backend_reporting_no_model_ids_is_not_a_warning(project: tuple[Path, Path]) -> None:
    """The Codex CLI's JSON stream carries no model fields. Silence is not a
    mixture, and reporting it as one would warn forever on that backend."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([None]))

    assert _journal(root)[-1]["brain_usage"]["models"] == {}

    check = _mixture(root, repo)
    assert check.status == "ok"
    assert "no model ids" in check.detail


def test_a_later_NEUTRAL_pass_does_not_displace_the_model_evidence(
    project: tuple[Path, Path],
) -> None:
    """A `noop` attempts nothing, so skipping it and reporting the real pass
    before it is correct. This is the half of the anchor that was right."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN, AUX], [MAIN]]))

    # No unconsumed raw left, so this pass is a noop with no brain calls.
    engine.curate(root, "repo", _Brain([[MAIN, AUX]]))
    entries = _journal(root)
    assert entries[-1]["status"] == "noop"

    check = _mixture(root, repo)
    assert check.status == "warn", "the changed pass is still the newest SUBSTANTIVE one"


class _UninstrumentedBrain:
    """A brain with **no `usage` attribute at all** — the real codex/API shape.

    `_usage_now` returns None for these, so the engine writes no `brain_usage`
    key whatsoever. That is a different shape from "usage present, models empty",
    and conflating the two was the round-1 defect.
    """

    name = "fake-uninstrumented"

    def plan_json(self, system: str, user: str) -> dict:
        return PLAN

    def text(self, system: str, user: str) -> str:
        return "# Status\n\nbody"


def test_a_newer_UNINSTRUMENTED_pass_displaces_an_older_warning(
    project: tuple[Path, Path],
) -> None:
    """Round 1, P2-CORRECTNESS-002. Absent `brain_usage` does not mean "spent no
    calls" — the codex and API brains never report any. Scanning past a newer
    real pass to keep warning about an older one is precisely wrong after a
    backend switch: the stale warning describes a brain no longer in use."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN, AUX], [MAIN]]))
    assert _mixture(root, repo).status == "warn"

    _write_raw(root, "r2.md", when="2026-08-05T13:00:00Z")
    engine.curate(root, "repo", _UninstrumentedBrain())
    newest = _journal(root)[-1]
    assert newest["status"] == "ok"
    assert "brain_usage" not in newest, "the fixture must reproduce the real absent-usage shape"

    check = _mixture(root, repo)
    assert check.status == "ok"
    assert "no model telemetry" in check.detail
    assert str(newest["at"]) in check.detail, "it must report the NEWER pass"
    assert "the model set changed" not in check.detail


def test_a_brain_spending_RESYNTH_displaces_an_older_warning(
    project: tuple[Path, Path],
) -> None:
    """`--resynth` spends a real budgeted brain call in `_synthesize` but
    journals no usage (`curator/engine.py:388`), so it is the in-tree case of a
    substantive pass with no telemetry."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN, AUX], [MAIN]]))
    assert _mixture(root, repo).status == "warn"

    engine.curate(root, "repo", _Brain([[MAIN, AUX]]), resynth=True)
    newest = _journal(root)[-1]
    assert newest["status"] == "resynth"
    assert "brain_usage" not in newest, "resynth journals no usage — the premise of this test"

    check = _mixture(root, repo)
    assert check.status == "ok"
    assert str(newest["at"]) in check.detail
    assert "the model set changed" not in check.detail


# --- states `curate health` already diagnoses: stay silent ------------------


@pytest.mark.parametrize("scenario", ["no-store", "not-enabled", "no-passes"])
def test_it_emits_nothing_where_curate_health_already_speaks(tmp_path: Path, scenario: str) -> None:
    """Two lines saying "health unknown" tell the reader nothing the first did
    not. Silence here is the deliberate behaviour, not an oversight."""
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    repo.mkdir()
    if scenario != "no-store":
        projects.register_project(root, repo)
        store.ensure_tree("repo", root)
    if scenario == "not-enabled":
        repo = tmp_path / "elsewhere"
        repo.mkdir()

    assert diagnostics._model_mixture_check(root, repo) == []


class _FailingPlanBrain(_Brain):
    """Records usage on the planning call, then fails the plan — the shape a
    failed `--dry-run` preview journals (`status: error`, `dry_run: true`, with
    whatever usage accumulated before the failure)."""

    def plan_json(self, system: str, user: str) -> dict:
        self._record()
        raise BrainError("plan boom")


def test_a_failed_DRY_RUN_never_displaces_the_real_pass(project: tuple[Path, Path]) -> None:
    """Round 2, P2-CORRECTNESS-002. A failed preview journals `status: "error"`,
    which is not a neutral status — but it carries `dry_run: true`, the flag the
    engine added specifically "so a health consumer does not read a failed
    *preview* as a failed pass" (`curator/engine.py:551-560`). A status-only
    anchor let a read-only preview replace the diagnosis for the real pass."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN, AUX], [MAIN]]))
    before = _mixture(root, repo)
    assert before.status == "warn"

    _write_raw(root, "r2.md", when="2026-08-05T13:30:00Z")
    engine.curate(root, "repo", _FailingPlanBrain([[MAIN]]), dry_run=True)

    preview = _journal(root)[-1]
    assert preview["status"] == "error" and preview["dry_run"] is True
    assert preview.get("brain_usage", {}).get("calls"), (
        "the preview must have spent a call, or it cannot displace anything"
    )

    after = _mixture(root, repo)
    assert after == before, "a preview changes nothing on disk and must change no verdict"


def _append_log_line(root: Path, line: str) -> None:
    """Append a raw line to the curator log, exactly as a corrupt/foreign writer
    would leave it — complete with the trailing newline, so it is NOT excusable
    as a torn final append."""
    path = store.memory_dir("repo", root) / store.CURATOR_LOG
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def test_a_DAMAGED_history_silences_the_check(project: tuple[Path, Path]) -> None:
    """Round 1, P2-OBSERVABILITY-003. `_read_curator_log` returns valid entries
    ALONGSIDE `DAMAGED`, so an early return that suppressed only unreadable /
    unparseable / empty let the check print a confident model verdict next to
    curate health's "history is damaged — health unknown"."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN, AUX], [MAIN]]))
    assert _mixture(root, repo).status == "warn", "it must speak before the damage"

    _append_log_line(root, "{ this is not json")

    entries, state = diagnostics._read_curator_log(
        store.memory_dir("repo", root) / store.CURATOR_LOG
    )
    assert state is diagnostics.LogState.DAMAGED
    assert entries, "the damaged log must still yield the usable records, or this proves nothing"
    assert diagnostics._curate_health_check(root, repo).status == "warn"
    assert diagnostics._model_mixture_check(root, repo) == []


def test_an_UNCLASSIFIABLE_status_silences_the_check(project: tuple[Path, Path]) -> None:
    """A well-formed record whose status this build does not recognize leaves the
    log `OK`, but curate health still reports "health unknown". The mixture check
    must not contradict it with a confident verdict."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN, AUX], [MAIN]]))
    assert _mixture(root, repo).status == "warn"

    _append_log_line(root, json.dumps({"status": "teleported", "at": "2026-08-05T14:00:00Z"}))

    _, state = diagnostics._read_curator_log(store.memory_dir("repo", root) / store.CURATOR_LOG)
    assert state is diagnostics.LogState.OK, "an unknown status is well-formed, not damage"
    health = diagnostics._curate_health_check(root, repo)
    assert health.status == "warn" and "does not recognize" in health.detail
    assert diagnostics._model_mixture_check(root, repo) == []


@pytest.mark.parametrize("value", [True, False])
def test_a_JSON_BOOLEAN_count_can_never_read_as_healthy(
    project: tuple[Path, Path], value: bool
) -> None:
    """Round 1, P2-CORRECTNESS-004. `bool` subclasses `int`, so `isinstance(True,
    int)` is True and `True == 1` — which let `{"calls": 1, "models": {m: true}}`
    through as a clean pass. A corrupt record must never launder into an `ok`."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN]]))

    _append_log_line(
        root,
        json.dumps(
            {
                "status": "ok",
                "at": "2026-08-05T15:00:00Z",
                "brain_usage": {"calls": 1, "models": {MAIN: value}},
            }
        ),
    )

    check = _mixture(root, repo)
    assert check.status == "warn"
    assert "malformed model counts" in check.detail


@pytest.mark.parametrize("value", [True, False])
def test_a_JSON_BOOLEAN_call_total_is_unusable_not_a_pass_to_skip(
    project: tuple[Path, Path], value: bool
) -> None:
    """A boolean `calls` must be reported as unusable — not silently skipped in
    favour of an older record, which would resurrect the stale-anchor bug."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN, AUX], [MAIN]]))

    _append_log_line(
        root,
        json.dumps(
            {
                "status": "ok",
                "at": "2026-08-05T15:00:00Z",
                "brain_usage": {"calls": value, "models": {MAIN: 1}},
            }
        ),
    )

    check = _mixture(root, repo)
    assert check.status == "warn"
    assert "unusable call total" in check.detail
    assert "the model set changed" not in check.detail, "the older pass must not be reported"


@pytest.mark.parametrize(
    "usage",
    [True, False, "usage", ["calls"], {}, 0, None],
)
def test_a_malformed_usage_CONTAINER_is_never_healthy(
    project: tuple[Path, Path], usage: object
) -> None:
    """Round 2, P2-CORRECTNESS-004. Validating the counters was not enough: a
    scalar, list, null or empty dict at the `brain_usage` level took the "recorded
    no model telemetry" branch and read as healthy. `BrainUsage.as_dict()` always
    writes a populated object, and a genuinely uninstrumented pass omits the key
    entirely — so a present-but-malformed value is corruption, not absence."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN]]))

    _append_log_line(
        root,
        json.dumps({"status": "ok", "at": "2026-08-05T15:00:00Z", "brain_usage": usage}),
    )

    check = _mixture(root, repo)
    assert check.status == "warn"
    assert "malformed usage block" in check.detail


@pytest.mark.parametrize("models", [True, "main", ["main"], 3, None])
def test_a_malformed_MODELS_container_is_never_healthy(
    project: tuple[Path, Path], models: object
) -> None:
    """The same laundering one level down: `{"calls": 2, "models": true}` gave the
    classifier empty buckets and reported "backend reported no model ids" — an
    `ok`. Only a real dict may reach any healthy conclusion."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN]]))

    _append_log_line(
        root,
        json.dumps(
            {
                "status": "ok",
                "at": "2026-08-05T15:00:00Z",
                "brain_usage": {"calls": 2, "models": models},
            }
        ),
    )

    check = _mixture(root, repo)
    assert check.status == "warn"
    assert "malformed model map" in check.detail


def test_an_ABSENT_usage_key_stays_healthy(project: tuple[Path, Path]) -> None:
    """The other half of P2-CORRECTNESS-004: tightening the container check must
    NOT turn the legitimate uninstrumented-backend case into a warning."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _UninstrumentedBrain())

    assert "brain_usage" not in _journal(root)[-1]
    check = _mixture(root, repo)
    assert check.status == "ok"
    assert "no model telemetry" in check.detail


def test_the_check_is_wired_into_collect_checks(project: tuple[Path, Path]) -> None:
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN, AUX], [MAIN]]))

    from neurobase.core.config import Config

    checks = diagnostics.collect_checks(Config(), root, repo)
    mixture = [c for c in checks if c.name == "model mixture"]
    assert len(mixture) == 1 and mixture[0].status == "warn"


# --- unit-level: shapes the engine cannot produce but a JSON file can -------


def test_a_count_above_the_call_total_is_reported_not_dropped() -> None:
    """`BrainUsage.record` cannot produce this, but a hand-edited or corrupt
    journal can. Silently dropping it would erase a model from the report."""
    consistent, partial, impossible = diagnostics._model_mixture({"calls": 2, "models": {MAIN: 7}})
    assert (consistent, partial) == (frozenset(), [])
    assert impossible == [MAIN]


@pytest.mark.parametrize(
    "usage",
    [
        None,
        "not-a-dict",
        {},
        {"calls": 0, "models": {MAIN: 1}},
        {"calls": "two", "models": {MAIN: 1}},
        {"calls": 2, "models": "not-a-dict"},
    ],
)
def test_malformed_usage_never_raises_and_finds_nothing(usage: object) -> None:
    """Accounting is observability: a malformed block must not crash a
    diagnostic, and must not be read as a mixture either."""
    assert diagnostics._model_mixture(usage) == (frozenset(), [], [])


def test_a_non_integer_count_is_reported_as_malformed() -> None:
    assert diagnostics._model_mixture({"calls": 2, "models": {MAIN: "lots"}})[2] == [MAIN]


@pytest.mark.parametrize("count", [0, -1, 7, "lots", None, True, False])
def test_every_model_lands_in_exactly_one_bucket(count: object) -> None:
    """No count may match no branch. An earlier shape dropped `0` and negatives
    through every arm, so a model in a corrupt record disappeared from the report
    rather than being flagged — silence is the one outcome this must never have.
    """
    consistent, partial, impossible = diagnostics._model_mixture(
        {"calls": 2, "models": {MAIN: count}}
    )
    buckets = [MAIN in consistent, any(m == MAIN for m, _ in partial), MAIN in impossible]
    assert sum(buckets) == 1, f"count={count!r} landed in {sum(buckets)} buckets"
