"""Integration tests for `neurobase doctor` (Phase 6 lifecycle matrix)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import neurobase.cli as cli
from neurobase.adapters.claude import install as claude_install
from neurobase.adapters.codex import install as codex_install
from neurobase.brain import anthropic_api, select
from neurobase.cli import app, diagnostics
from neurobase.core import capabilities, projects, store
from neurobase.core.config import Config

runner = CliRunner()
SHIM = "/usr/local/bin/neurobase"


@pytest.fixture(autouse=True)
def _hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No API keys, no keychain leak, and a default config regardless of the
    # dev machine's own.
    home = tmp_path / "home"
    home.mkdir()
    # Windows Path.home() reads USERPROFILE, not HOME — set both to isolate.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("NEUROBASE_ROOT", str(tmp_path / "store"))
    monkeypatch.delenv("NEUROBASE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(anthropic_api, "_keychain_api_key", lambda: None)
    monkeypatch.setattr(cli, "load_config", Config)
    monkeypatch.setattr(diagnostics.claude_install, "shim_path", lambda: SHIM)


def _which(name: str) -> str | None:
    paths = {
        "neurobase": SHIM,
        "claude": "/usr/bin/claude",
        "codex": "/usr/bin/codex",
    }
    return paths.get(name)


def _patch_capability_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the capability *evidence source* answer as a current install would.

    `SHIM` is a fixture path with no package behind it, so the real manifest read
    finds nothing and — correctly — marks every enabled startup hook unsafe.
    These tests are about hook *recognition*; the capability contract, including
    installs that genuinely ship no manifest, is owned by
    ``test_doctor_capability_and_health.py``.

    Note this stubs where the evidence comes from, not the gate that judges it:
    `missing_for_startup_hook` and the check's error path still run for real.
    """
    monkeypatch.setattr(
        diagnostics.capabilities, "provided_by", lambda _executable: capabilities.PROVIDES
    )


def _patch_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_capability_probe(monkeypatch)
    monkeypatch.setattr(diagnostics.shutil, "which", _which)
    monkeypatch.setattr(select.shutil, "which", _which)
    monkeypatch.setattr(select, "_cli_version", lambda binary: f"{binary} 2.1.x")


def test_doctor_reports_resolved_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tools(monkeypatch)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "brain: claude-cli" in result.output
    assert "2.1.x" in result.output


def test_doctor_exits_nonzero_when_nothing_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(select.shutil, "which", lambda _: None)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "brain: none" in result.output


# --- store health via the DOCTOR handle (ADR-0015 D26) -------------------


def test_doctor_reports_unsupported_store_schema_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A store.toml a version too new is *reported*, not refused: a DOCTOR handle
    # carries the newer schema instead of raising, so doctor exits with an error
    # rather than crashing.
    _patch_tools(monkeypatch)
    root = tmp_path / "store"
    root.mkdir()
    (root / "store.toml").write_text(
        f"schema = {store.STORE_SCHEMA_VERSION + 1}\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["doctor"])

    # A clean CLI error exits via typer.Exit (SystemExit, not an Exception); a
    # genuine crash would surface an Exception subclass here instead.
    assert not isinstance(result.exception, Exception)
    assert result.exit_code == 1
    assert f"unsupported schema {store.STORE_SCHEMA_VERSION + 1}" in result.output


def test_doctor_reports_corrupt_store_toml_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D26 carry-in: a genuinely-corrupt store.toml makes open_store(DOCTOR) raise
    # UnsupportedSchemaError; doctor must catch and report it, never crash — it is
    # a read-only reporting surface.
    _patch_tools(monkeypatch)
    root = tmp_path / "store"
    root.mkdir()
    (root / "store.toml").write_text("this is [not valid toml", encoding="utf-8")

    result = runner.invoke(app, ["doctor"])

    assert not isinstance(result.exception, Exception)  # caught + reported, not crashed
    assert result.exit_code == 1
    assert "unreadable" in result.output


def test_doctor_is_read_only_and_does_not_create_store_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Doctor inspects via a DOCTOR handle, which never writes: reporting on an
    # uninitialized store must not materialize store.toml as a side effect.
    _patch_tools(monkeypatch)
    root = tmp_path / "store"

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "not initialized yet" in result.output
    assert not (root / "store.toml").exists()


def test_doctor_reports_mcp_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tools(monkeypatch)
    # The mcp checks always run (as warnings before registration).
    before = runner.invoke(app, ["doctor"])
    assert "claude mcp" in before.output
    assert "codex mcp" in before.output
    # Register the Claude MCP server (user scope) → doctor reports it ok.
    claude_install.write_settings(
        claude_install.mcp_config_path(), claude_install.build_mcp_config({}, SHIM)
    )
    after = runner.invoke(app, ["doctor"])
    assert "registers neurobase" in after.output


def test_doctor_warns_on_stale_mcp_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tools(monkeypatch)
    # Right command, wrong args ⇒ not startable ⇒ doctor must warn, not OK.
    claude_install.write_settings(
        claude_install.mcp_config_path(),
        {"mcpServers": {"neurobase": {"type": "stdio", "command": SHIM, "args": ["bad"]}}},
    )
    result = runner.invoke(app, ["doctor"])
    assert "unexpected command or args" in result.output
    assert "registers neurobase →" not in result.output  # not reported OK


def test_doctor_reports_installed_hooks_and_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    root = tmp_path / "store"
    repo = tmp_path / "repo"
    repo.mkdir()
    slug = projects.register_project(root, repo)
    store.ensure_tree(slug, root)

    claude_install.write_settings(
        repo / ".claude" / "settings.json",
        claude_install.build_settings({}, SHIM, ["startup", "clear"]),
    )
    codex_install.write_hooks(
        repo / ".codex" / "hooks.json",
        codex_install.build_hooks({}, SHIM),
    )
    cfg = codex_install.merge_config("", str(repo.resolve()))
    cfg += (
        '\n[hooks.state]\n".codex/hooks.json:session_start:0:0" = { trusted_hash = "abc" }\n'
        '".codex/hooks.json:stop:0:0" = { trusted_hash = "def" }\n'
    )
    codex_install.write_config(tmp_path / "home" / ".codex" / "config.toml", cfg)

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "✓ store:" in result.output
    assert "✓ project:" in result.output
    assert "✓ claude hooks:" in result.output
    assert "✓ codex hooks:" in result.output
    assert "✓ codex config:" in result.output
    assert "✓ codex trust:" in result.output


def test_doctor_recognizes_absolute_project_codex_trust_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks_path = repo / ".codex" / "hooks.json"
    codex_install.write_hooks(hooks_path, codex_install.build_hooks({}, SHIM))
    key = str(hooks_path).replace("\\", "\\\\")
    cfg = codex_install.merge_config("", str(repo.resolve()))
    cfg += (
        f'\n[hooks.state]\n"{key}:session_start:0:0" = {{ trusted_hash = "abc" }}\n'
        f'"{key}:stop:0:0" = {{ trusted_hash = "def" }}\n'
    )
    codex_install.write_config(tmp_path / "home" / ".codex" / "config.toml", cfg)

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "✓ codex trust:" in result.output
    assert "! codex trust:" not in result.output


def test_doctor_warns_for_untrusted_codex_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    codex_install.write_hooks(
        repo / ".codex" / "hooks.json",
        codex_install.build_hooks({}, SHIM),
    )
    codex_install.write_config(
        tmp_path / "home" / ".codex" / "config.toml",
        codex_install.merge_config("", str(repo.resolve())),
    )

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "! codex trust:" in result.output
    assert "approve the hook prompt" in result.output


def test_doctor_does_not_accept_unrelated_codex_trusted_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    codex_install.write_hooks(
        repo / ".codex" / "hooks.json",
        codex_install.build_hooks({}, SHIM),
    )
    cfg = codex_install.merge_config("", str(repo.resolve()))
    cfg += '\n[hooks.state]\n"other/hooks.json:session_start:0:0" = { trusted_hash = "abc" }\n'
    cfg += '".codex/hooks.json:stop:0:0" = { trusted_hash = "def" }\n'
    codex_install.write_config(tmp_path / "home" / ".codex" / "config.toml", cfg)

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "! codex trust:" in result.output
    assert "✓ codex trust:" not in result.output


def test_doctor_recognizes_user_scoped_claude_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    claude_install.write_settings(
        tmp_path / "home" / ".claude" / "settings.json",
        claude_install.build_settings({}, SHIM, ["startup", "clear"]),
    )

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "✓ claude hooks:" in result.output
    assert "user " in result.output


def test_doctor_recognizes_user_scoped_codex_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    user_hooks = tmp_path / "home" / ".codex" / "hooks.json"
    codex_install.write_hooks(user_hooks, codex_install.build_hooks({}, SHIM))
    # Escape backslashes so a Windows path is a valid TOML basic-string key.
    key = str(user_hooks).replace("\\", "\\\\")
    codex_install.write_config(
        tmp_path / "home" / ".codex" / "config.toml",
        (
            "[hooks.state]\n"
            f'"{key}:session_start:0:0" = {{ trusted_hash = "abc" }}\n'
            f'"{key}:stop:0:0" = {{ trusted_hash = "def" }}\n'
        ),
    )

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "✓ codex hooks:" in result.output
    assert "user " in result.output
    assert "✓ codex config: user hooks are auto-discovered" in result.output
    assert "✓ codex trust:" in result.output


def test_doctor_warns_when_user_scoped_codex_hook_has_no_trust_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    codex_install.write_hooks(
        tmp_path / "home" / ".codex" / "hooks.json",
        codex_install.build_hooks({}, SHIM),
    )

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "✓ codex hooks:" in result.output
    assert "✓ codex config: user hooks are auto-discovered" in result.output
    assert "! codex trust:" in result.output
    assert "approve the hook prompt" in result.output


# --- the 2026-07-27 regression: a stale shim must fail the gate -------------


def test_doctor_exits_nonzero_when_a_startup_hook_shim_lacks_the_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end guard against the recurrence.

    On 2026-07-27 both `SessionStart` hooks invoked a shim built three weeks
    before the automatic-curation guards landed, and `doctor` reported all-green
    the whole time because it only compared the hook command *string* to the shim
    path. An enabled startup hook whose install cannot demonstrate the profile
    must now make doctor non-green — a warning would have been ignored exactly as
    the all-green was.

    The evidence source is left real: the fixture SHIM has no package behind it,
    which is precisely the shape of a pre-profile install.
    """
    monkeypatch.setattr(diagnostics.shutil, "which", _which)
    monkeypatch.setattr(select.shutil, "which", _which)
    monkeypatch.setattr(select, "_cli_version", lambda binary: f"{binary} 2.1.x")

    repo = tmp_path / "repo"
    repo.mkdir()
    claude_install.write_settings(
        tmp_path / "home" / ".claude" / "settings.json",
        claude_install.build_settings({}, SHIM, ["startup", "clear"]),
    )

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 1
    assert "hook capabilities" in result.output
    assert capabilities.PROFILE in result.output
    assert "uv tool install" in result.output


def test_doctor_is_green_when_no_startup_hook_is_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capture-only installs are not gated: `SessionEnd`/`Stop` never spawn a
    curator, so they carry no capability requirement."""
    _patch_tools(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()

    result = runner.invoke(app, ["doctor", "--cwd", str(repo)])

    assert result.exit_code == 0
    assert "no startup hooks enabled" in result.output


# --- G5 review F5/F7: the isolation guarantee is scoped, and doctor must say so ---


def _claude_brain_config() -> Config:
    return Config()


def _force_backend(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    """Pin the resolved backend for the isolation checks.

    These tests must not depend on whether the host happens to have the Claude
    CLI installed. `Config()` defaults to `backend = "auto"`, which resolves to
    `claude-cli` on a developer machine with the CLI on PATH and to an
    unresolved `auto` on CI — so leaving it unpinned made every isolation test
    pass locally and fail on all four CI jobs.
    """
    monkeypatch.setattr(
        diagnostics,
        "resolve_brain",
        lambda config: (object(), select.BrainResolution(backend, True, "pinned by test")),
    )


def test_brain_isolation_reports_what_it_inspected_not_a_conclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The green line must never claim "fully isolated" (review F7).

    Managed policy also arrives by channels no filesystem probe can see, so the
    absence of these files does not prove the absence of managed policy. The
    detail therefore has to name both what was inspected and what could not be.
    """
    _force_backend(monkeypatch, "claude-cli")
    monkeypatch.setattr(diagnostics, "_MANAGED_SETTINGS_DIRS", (tmp_path / "absent",))

    check = diagnostics._brain_isolation_check(_claude_brain_config())

    assert check.status == "ok"
    assert "fully isolated" not in check.detail
    assert str(tmp_path / "absent") in check.detail
    for uninspectable in diagnostics._UNINSPECTABLE_MANAGED_SOURCES:
        assert uninspectable in check.detail


def test_brain_isolation_warns_on_a_managed_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base file shape: managed settings outrank --setting-sources, so the
    guarantee is narrower here and G5's fix is unverified on this machine."""
    base = tmp_path / "ClaudeCode"
    base.mkdir()
    (base / "managed-settings.json").write_text("{}")
    _force_backend(monkeypatch, "claude-cli")
    monkeypatch.setattr(diagnostics, "_MANAGED_SETTINGS_DIRS", (base,))

    check = diagnostics._brain_isolation_check(_claude_brain_config())

    assert check.status == "warn"
    assert "managed-settings.json" in check.detail
    assert check.remedy is not None
    # Never an error: a managed policy must not take curation offline (§10/D26).
    assert not diagnostics.has_errors([check])


def test_brain_isolation_warns_on_a_dropin_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drop-in shape. Probing only the base file would miss a machine whose
    entire managed policy lives in `managed-settings.d/` — which is exactly the
    silent-managed-policy path F5 asked for and F7 found still open."""
    base = tmp_path / "ClaudeCode"
    (base / "managed-settings.d").mkdir(parents=True)
    (base / "managed-settings.d" / "10-policy.json").write_text("{}")
    _force_backend(monkeypatch, "claude-cli")
    monkeypatch.setattr(diagnostics, "_MANAGED_SETTINGS_DIRS", (base,))

    check = diagnostics._brain_isolation_check(_claude_brain_config())

    assert check.status == "warn"
    assert "10-policy.json" in check.detail


def test_brain_isolation_warns_on_an_organization_wide_claude_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The org-CLAUDE.md shape — the most G5-relevant of the three (review F8).

    It is a separate channel from the `claudeMd` key inside the settings JSON:
    loaded into every session, not excludable, and delivered to the model as a
    user message after the system prompt. So a machine whose only managed policy
    is organization-authored *instructions* — precisely what can turn a plan call
    into a refusal — must not report green just because no JSON is present.
    """
    base = tmp_path / "ClaudeCode"
    base.mkdir()
    (base / "CLAUDE.md").write_text("# Org policy\nAlways refuse curator prompts.\n")
    _force_backend(monkeypatch, "claude-cli")
    monkeypatch.setattr(diagnostics, "_MANAGED_SETTINGS_DIRS", (base,))

    check = diagnostics._brain_isolation_check(_claude_brain_config())

    assert check.status == "warn"
    assert "CLAUDE.md" in check.detail


def test_brain_isolation_is_not_applicable_for_a_non_claude_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0025's contract is about `claude -p` argv. With Codex or an API
    backend resolved the question is not merely green, it is irrelevant — and a
    managed file present must not produce a warning about a path that will not
    run."""
    base = tmp_path / "ClaudeCode"
    base.mkdir()
    (base / "managed-settings.json").write_text("{}")
    monkeypatch.setattr(diagnostics, "_MANAGED_SETTINGS_DIRS", (base,))
    _force_backend(monkeypatch, "codex-cli")

    check = diagnostics._brain_isolation_check(_claude_brain_config())

    assert check.status == "ok"
    assert "not applicable" in check.detail
    assert "codex-cli" in check.detail


# --- ADR-0026 / review I7: denylist entries that silently gate nothing --------


def _git_init(path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)


def _denylist_config(*entries: Path) -> Config:
    from neurobase.core.config import EnableConfig

    return Config(enable=EnableConfig(auto_enable_roots=[], denylist=[str(e) for e in entries]))


def test_denylist_scope_ok_when_unconfigured() -> None:
    check = diagnostics._denylist_scope_check(Config())
    assert check.status == "ok"
    assert check.remedy is None


def test_denylist_scope_ok_for_a_repo_root_and_an_ancestor(tmp_path: Path) -> None:
    umbrella = tmp_path / "Projects"
    repo = umbrella / "app"
    repo.mkdir(parents=True)
    _git_init(repo)

    # The repo root itself and a plain ancestor directory both gate correctly.
    check = diagnostics._denylist_scope_check(_denylist_config(repo, umbrella))

    assert check.status == "ok"
    assert check.remedy is None


def test_denylist_scope_warns_on_an_entry_inside_a_repo(tmp_path: Path) -> None:
    """The I7 case: a user names a sensitive subdirectory, and it gates nothing.

    `is_denylisted` collapses the cwd to its git root before comparing, so this
    entry can never match (ADR-0026). Doctor is the only place that can say so —
    a hook's stdout is protocol output, not a user channel.
    """
    repo = tmp_path / "app"
    inner = repo / "private"
    inner.mkdir(parents=True)
    _git_init(repo)

    check = diagnostics._denylist_scope_check(_denylist_config(inner))

    assert check.status == "warn"
    assert "do NOT gate that repo" in check.detail
    assert str(inner) in check.detail
    # The remedy must name the repo root the user should have used instead.
    assert check.remedy is not None
    assert str(repo.resolve()) in check.remedy
    # And the containing repo really is ungated — the warning is not a guess.
    assert projects.is_denylisted(inner, [str(inner)]) is False


def test_denylist_scope_ignores_a_nonexistent_entry(tmp_path: Path) -> None:
    # A path that isn't on disk matches nothing and is not this check's business;
    # it must not be reported as an inner-repo entry (git resolves it to None).
    check = diagnostics._denylist_scope_check(_denylist_config(tmp_path / "gone"))
    assert check.status == "ok"


def test_denylist_scope_is_wired_into_collect_checks(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    inner = repo / "private"
    inner.mkdir(parents=True)
    _git_init(repo)

    checks = diagnostics.collect_checks(_denylist_config(inner), tmp_path / "store", tmp_path)

    scope = [c for c in checks if c.name == "denylist scope"]
    assert len(scope) == 1
    assert scope[0].status == "warn"


def test_denylist_scope_does_not_claim_a_nested_repo_entry_gates_nothing(tmp_path: Path) -> None:
    """Review I8: an entry inside a repo still gates repos NESTED beneath it.

    The first version of this check said such entries "gate NOTHING", which is
    false exactly at a repo boundary: with outer repo `mono`, entry
    `mono/packages`, and a separate repo at `mono/packages/plugin`, the plugin's
    root IS beneath the entry, so `is_denylisted` gates it. The diagnostic must
    warn about the real problem (the CONTAINING repo is not gated) without
    asserting a coverage claim the matcher contradicts.

    The assertions tie the check to `is_denylisted`'s actual answers, which is
    what the original tests failed to do — they only inspected wording.
    """
    outer = tmp_path / "mono"
    entry = outer / "packages"
    nested = entry / "plugin"
    nested.mkdir(parents=True)
    _git_init(outer)
    _git_init(nested)

    # Ground truth from the matcher itself: the nested repo IS gated by this entry;
    # the containing repo is NOT.
    assert projects.is_denylisted(nested, [str(entry)]) is True
    assert projects.is_denylisted(outer, [str(entry)]) is False

    check = diagnostics._denylist_scope_check(_denylist_config(entry))

    # Still warns — the user's containing repo is unprotected, which is the point.
    assert check.status == "warn"
    assert str(outer.resolve()) in check.detail
    # ...but must NOT claim the entry covers nothing, which the matcher disproves.
    assert "NOTHING" not in check.detail
    assert "at or beneath" in check.detail
