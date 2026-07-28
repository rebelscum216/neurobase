"""The safety-capability profile, and the behaviors each name is a claim about.

The profile is only worth anything if `PROVIDES` cannot drift from what the
build actually does. Every capability name therefore gets a test that exercises
the *behavior*, not the module's existence — that distinction is the entire
lesson of the 2026-07-27 recurrence, where the guards were present in source and
absent from the shim the hooks ran.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from neurobase.cli import app
from neurobase.core import capabilities

runner = CliRunner()


def test_describe_is_json_serializable_and_stable_shaped() -> None:
    payload = capabilities.describe()
    round_tripped = json.loads(json.dumps(payload))
    assert round_tripped["profile"] == capabilities.PROFILE
    assert sorted(round_tripped["provides"]) == sorted(capabilities.PROVIDES)
    assert isinstance(round_tripped["version"], str)


def test_required_is_a_subset_of_provides() -> None:
    """This build must satisfy its own startup-hook requirement, or `doctor` run
    against a correctly-installed current shim would report it unsafe."""
    assert capabilities.REQUIRED_FOR_STARTUP_HOOK <= capabilities.PROVIDES


def test_missing_for_startup_hook_reports_everything_when_nothing_provided() -> None:
    """A build predating the profile reports nothing — the case that matters."""
    assert capabilities.missing_for_startup_hook(set()) == sorted(
        capabilities.REQUIRED_FOR_STARTUP_HOOK
    )


def test_missing_for_startup_hook_ignores_extra_capabilities() -> None:
    """A *newer* executable advertising capabilities this build has never heard
    of is not thereby unsafe."""
    generous = set(capabilities.PROVIDES) | {"some-future-guard"}
    assert capabilities.missing_for_startup_hook(generous) == []


def test_capabilities_command_emits_the_profile() -> None:
    """The command doctor probes with. Its *absence* on an older build is the
    signal; its presence must therefore be parseable without ceremony."""
    result = runner.invoke(app, ["capabilities"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["profile"] == capabilities.PROFILE
    assert set(payload["provides"]) >= capabilities.REQUIRED_FOR_STARTUP_HOOK


# --- each capability name is a claim about behavior -------------------------


def test_hook_reentrancy_suppression_is_real(monkeypatch, capsys) -> None:
    """`hook-reentrancy-suppression`: a hook invoked from inside Neurobase's own
    subprocess must do nothing at all."""
    from neurobase.cli import run_hook

    monkeypatch.setenv("NEUROBASE_INTERNAL_CALL", "1")
    run_hook(["claude", "session-start"])
    assert capsys.readouterr().out == ""


def test_curate_single_flight_is_real(tmp_path) -> None:
    """`curate-single-flight`: the second holder of a project's curate lock is
    refused while the first holds it."""
    from neurobase.core import locks
    from neurobase.core.store_handle import StoreMode, open_store

    (tmp_path / "projects" / "p" / "memory").mkdir(parents=True)
    (tmp_path / "store.toml").write_text("schema = 1\n", encoding="utf-8")
    handle = open_store(tmp_path, StoreMode.DOCTOR)

    with locks.try_curate_lock(handle, "p") as first:
        assert first is True
        with locks.try_curate_lock(handle, "p") as second:
            assert second is False, "a second curator acquired the lock concurrently"


def test_automatic_pass_budget_is_real_and_tighter_than_manual() -> None:
    """`automatic-pass-budget`: a hook-triggered pass is bounded, and bounded
    more tightly than one a human typed."""
    from neurobase.core.config import CurateConfig
    from neurobase.curator import budget as curate_budget

    config = CurateConfig()
    automatic = curate_budget.from_config(config, automatic=True)
    manual = curate_budget.from_config(config, automatic=False)
    assert automatic.max_raws > 0
    assert automatic.max_brain_calls > 0
    assert automatic.max_raws <= manual.max_raws
    assert automatic.max_brain_calls <= manual.max_brain_calls


def test_project_store_write_lock_is_real(tmp_path) -> None:
    """`project-store-write-lock`: a non-blocking acquire is refused while
    another holder has the project's write lock (ADR-0023)."""
    from neurobase.core import lock as store_lock

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    with (
        store_lock.project_lock(memory_dir, blocking=True),
        pytest.raises(store_lock.LockContended),
        store_lock.project_lock(memory_dir, blocking=False),
    ):
        pass  # pragma: no cover - the second acquire must raise


@pytest.mark.parametrize("name", sorted(capabilities.PROVIDES))
def test_every_provided_capability_is_named_in_this_module(name: str) -> None:
    """A capability may only be advertised if it has a module-level constant —
    a weak but cheap guard against a bare string sneaking into PROVIDES without
    a documented contract."""
    constants = {
        value
        for key, value in vars(capabilities).items()
        if key.isupper() and isinstance(value, str)
    }
    assert name in constants
