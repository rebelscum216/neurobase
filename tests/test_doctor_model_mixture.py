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

from neurobase.brain.base import BrainUsage
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


def test_a_model_serving_only_part_of_the_pass_warns(project: tuple[Path, Path]) -> None:
    """The real signal: 0 < count < calls. Call 1 gets both models, call 2 only
    the main one, so the auxiliary covers half the pass."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN, AUX], [MAIN]]))

    usage = _journal(root)[-1]["brain_usage"]
    calls = usage["calls"]
    assert usage["models"][MAIN] == calls
    assert 0 < usage["models"][AUX] < calls

    check = _mixture(root, repo)
    assert check.status == "warn"
    assert "changed mid-pass" in check.detail
    assert f"{AUX} on 1/{calls}" in check.detail
    assert MAIN not in check.detail, "the model that served every call is not the finding"
    assert check.remedy is not None and "no action is required" in check.remedy


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


def test_it_reports_the_last_pass_that_SPENT_calls_not_the_last_pass(
    project: tuple[Path, Path],
) -> None:
    """A later `noop` spends nothing and carries no usage. Anchoring on the last
    record would report "nothing to check" on a store whose last real pass mixed
    models."""
    root, repo = project
    _write_raw(root, "r1.md")
    engine.curate(root, "repo", _Brain([[MAIN, AUX], [MAIN]]))

    # No unconsumed raw left, so this pass is a noop with no brain calls.
    engine.curate(root, "repo", _Brain([[MAIN, AUX]]))
    entries = _journal(root)
    assert entries[-1]["status"] == "noop"
    assert "brain_usage" not in entries[-1] or not entries[-1]["brain_usage"].get("calls")

    check = _mixture(root, repo)
    assert check.status == "warn", "the mixed pass is still the last one that spent calls"


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


@pytest.mark.parametrize("count", [0, -1, 7, "lots", None])
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
