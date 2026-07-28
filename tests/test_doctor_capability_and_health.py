"""`doctor`'s two post-2026-07-27 checks: hook capabilities, and curate health.

Both exist because `doctor` reported all-green while the machine was unsafe — the
hooks ran a shim three weeks older than the guards it was assumed to have, and two
status nodes had been frozen for 7 and 11 days with every failure recorded only in
a log nothing read.

Every finding from review round 1 has a named regression here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neurobase.cli import diagnostics
from neurobase.core import capabilities

CURRENT = "/opt/current/bin/neurobase"
STALE = "/opt/stale/bin/neurobase"


# --- fake installs: a real package layout, so nothing is stubbed ------------


def _install(root: Path, *, provides: list[str] | None) -> str:
    """A console script beside a `site-packages/neurobase/`, as uv/venv/pipx lay
    it out — including the ``#!<root>/bin/python`` shebang a real one carries,
    since that shebang is what binds the manifest to this environment.

    ``provides=None`` ships **no manifest** — a build predating the profile,
    which is the case that matters.
    """
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "python").write_text("", encoding="utf-8")
    executable = bin_dir / "neurobase"
    executable.write_text(f"#!{bin_dir / 'python'}\n", encoding="utf-8")
    executable.chmod(0o755)
    package = root / "lib" / "python3.14" / "site-packages" / "neurobase"
    package.mkdir(parents=True, exist_ok=True)
    if provides is not None:
        (package / capabilities.MANIFEST_NAME).write_text(
            json.dumps({"profile": capabilities.PROFILE, "provides": provides}),
            encoding="utf-8",
        )
    return str(executable)


def _current_install(tmp_path: Path) -> str:
    return _install(tmp_path / "current", provides=sorted(capabilities.PROVIDES))


def _stale_install(tmp_path: Path) -> str:
    return _install(tmp_path / "stale", provides=None)


def _write_claude_hook(home: Path, executable: str, *, user: bool, repo: Path) -> None:
    base = home if user else repo
    settings = base / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup|clear",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"{executable} hook claude session-start",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    return r


# --- manifest reading (never execution) -------------------------------------


def test_manifest_is_read_from_the_installs_package(tmp_path: Path) -> None:
    executable = _current_install(tmp_path)
    assert capabilities.provided_by(executable) == capabilities.PROVIDES


def test_install_without_a_manifest_provides_nothing(tmp_path: Path) -> None:
    """A build predating the profile ships no manifest — absence is the signal,
    and it cannot be faked in the dangerous direction."""
    assert capabilities.provided_by(_stale_install(tmp_path)) == frozenset()


def test_missing_executable_provides_nothing(tmp_path: Path) -> None:
    assert capabilities.provided_by(tmp_path / "nope" / "neurobase") == frozenset()


def test_malformed_manifest_provides_nothing(tmp_path: Path) -> None:
    executable = _install(tmp_path / "broken", provides=[])
    package = tmp_path / "broken" / "lib" / "python3.14" / "site-packages" / "neurobase"
    (package / capabilities.MANIFEST_NAME).write_text("{ not json", encoding="utf-8")
    assert capabilities.provided_by(executable) == frozenset()


# --- R1-F1 (blocker): doctor must never execute a configured command --------


def _write_claude_hook_command(home: Path, command: str) -> None:
    """A user-scope Claude SessionStart hook with an arbitrary command string."""
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": command}]}]}}
        ),
        encoding="utf-8",
    )


def test_foreign_command_in_an_enabled_scope_is_rejected_by_ownership(
    home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Round-1 blocker regression, made non-vacuous in round 6.**

    An arbitrary binary named in hook config was collected and executed, creating
    a file on disk. The command is placed in an **enabled** (user) scope here, so
    the ownership predicate is genuinely the thing that must reject it — an
    earlier version put it in an unwired Codex file, where the enablement filter
    excluded it first and the test passed with ownership disabled.
    """
    _write_claude_hook_command(home, "/usr/bin/touch hook claude session-start")

    assert diagnostics._startup_hook_executables(repo) == []
    assert [c.status for c in diagnostics._capability_checks(repo)] == ["ok"]

    # Control: with ownership made permissive the very same command *is*
    # collected, proving the assertion above is the ownership filter's doing.
    monkeypatch.setattr(diagnostics.claude_install, "is_owned_command", lambda _command: True)
    collected = diagnostics._startup_hook_executables(repo)
    assert [executable for _label, executable in collected] == ["/usr/bin/touch"]


def test_unwired_codex_project_hooks_are_not_enabled(home: Path, repo: Path) -> None:
    """Codex only discovers a project hooks file that `config.toml` wires *and*
    trusts. An inert file is not an enabled hook."""
    hooks = repo / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"{STALE} hook codex session-start",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    assert diagnostics._startup_hook_executables(repo) == []


# --- R1-F2 (major): every scope, not one per agent --------------------------


def test_stale_user_hook_beside_current_project_hook_is_reported(
    tmp_path: Path, home: Path, repo: Path
) -> None:
    """**Round-1 F2 regression.**

    Claude runs matching hooks from *both* scopes. Keying by agent and
    `setdefault`-ing hid the stale executable behind the current one's green
    check — a false green on exactly the condition the gate exists to catch.
    """
    current = _current_install(tmp_path)
    stale = _stale_install(tmp_path)
    _write_claude_hook(home, current, user=False, repo=repo)
    _write_claude_hook(home, stale, user=True, repo=repo)

    collected = diagnostics._startup_hook_executables(repo)
    assert {executable for _label, executable in collected} == {current, stale}

    checks = diagnostics._capability_checks(repo)
    assert sorted(c.status for c in checks) == ["error", "ok"]
    failing = next(c for c in checks if c.status == "error")
    assert stale in failing.detail
    for required in capabilities.REQUIRED_FOR_STARTUP_HOOK:
        assert required in failing.detail


def test_identical_executable_in_both_scopes_reports_once(
    tmp_path: Path, home: Path, repo: Path
) -> None:
    current = _current_install(tmp_path)
    _write_claude_hook(home, current, user=False, repo=repo)
    _write_claude_hook(home, current, user=True, repo=repo)
    checks = diagnostics._capability_checks(repo)
    assert [c.status for c in checks] == ["ok"]
    assert "project" in checks[0].detail and "user" in checks[0].detail


def test_no_startup_hooks_needs_no_capabilities(home: Path, repo: Path) -> None:
    checks = diagnostics._capability_checks(repo)
    assert [c.status for c in checks] == ["ok"]
    assert "no startup hooks enabled" in checks[0].detail


def test_current_install_satisfies_the_profile(tmp_path: Path, home: Path, repo: Path) -> None:
    _write_claude_hook(home, _current_install(tmp_path), user=True, repo=repo)
    checks = diagnostics._capability_checks(repo)
    assert [c.status for c in checks] == ["ok"]
    assert capabilities.PROFILE in checks[0].detail


def test_partial_capabilities_name_only_what_is_missing(
    tmp_path: Path, home: Path, repo: Path
) -> None:
    executable = _install(tmp_path / "partial", provides=[capabilities.HOOK_REENTRANCY_SUPPRESSION])
    _write_claude_hook(home, executable, user=True, repo=repo)
    checks = diagnostics._capability_checks(repo)
    assert [c.status for c in checks] == ["error"]
    missing_clause = checks[0].detail.split("is missing")[1]
    assert capabilities.CURATE_SINGLE_FLIGHT in missing_clause
    assert capabilities.HOOK_REENTRANCY_SUPPRESSION not in missing_clause


def test_executable_path_with_spaces_is_extracted(tmp_path: Path, home: Path, repo: Path) -> None:
    spaced = _install(tmp_path / "a b", provides=sorted(capabilities.PROVIDES))
    assert " " in spaced
    _write_claude_hook(home, spaced, user=True, repo=repo)
    collected = diagnostics._startup_hook_executables(repo)
    assert [executable for _label, executable in collected] == [spaced]


# --- R1-F3 (major): the curator's real status vocabulary --------------------


def _store(tmp_path: Path, entries: list[dict]) -> tuple[Path, Path]:
    root = tmp_path / "store"
    repo = tmp_path / "curated-repo"
    repo.mkdir()
    memory = root / "projects" / "p" / "memory"
    memory.mkdir(parents=True)
    (root / "store.toml").write_text("schema = 1\n", encoding="utf-8")
    (root / "registry.toml").write_text(
        f'[projects.p]\nroots = [\n    "{repo}",\n]\n', encoding="utf-8"
    )
    (memory / ".curator-log.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
    )
    return root, repo


def test_noops_after_success_are_not_failures(tmp_path: Path) -> None:
    """**Round-1 F3 regression.** Three noops after an `ok` were reported as
    "3 failed pass(es)" with a non-zero exit, though nothing failed."""
    root, repo = _store(
        tmp_path,
        [{"at": "2026-07-27T10:00:00Z", "status": "ok"}]
        + [{"at": f"2026-07-27T1{i}:00:00Z", "status": "noop", "raw": 0} for i in range(1, 4)],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "ok"


def test_resynth_counts_as_recovery(tmp_path: Path) -> None:
    """A successful regeneration clears the frozen node, so it clears health."""
    root, repo = _store(
        tmp_path,
        [
            {"at": "2026-07-20T10:00:00Z", "status": "ok"},
            {"at": "2026-07-21T10:00:00Z", "status": "error", "error": "boom"},
            {"at": "2026-07-22T10:00:00Z", "status": "resynth", "active_facts": 12},
        ],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "ok"
    assert "2026-07-22T10:00:00Z" in check.detail


def test_trailing_noop_does_not_mask_the_real_failure_reason(tmp_path: Path) -> None:
    """A noop landing after real failures must not become the displayed reason."""
    root, repo = _store(
        tmp_path,
        [
            {"at": "2026-07-20T10:00:00Z", "status": "ok"},
            {"at": "2026-07-21T10:00:00Z", "status": "error", "error": "claude -p timed out"},
            {"at": "2026-07-22T10:00:00Z", "status": "noop", "raw": 0},
        ],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "claude -p timed out" in check.detail
    assert "1 failed pass" in check.detail


def test_partial_is_a_failure(tmp_path: Path) -> None:
    """`partial` consumes raws then dies before synthesis — exactly the shape
    that leaves a node frozen while facts move on."""
    root, repo = _store(
        tmp_path,
        [
            {"at": "2026-07-20T10:00:00Z", "status": "ok"},
            {"at": "2026-07-21T10:00:00Z", "status": "partial", "error": "budget exhausted"},
        ],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "1 failed pass" in check.detail


def test_unknown_future_status_is_unknown_not_healthy(tmp_path: Path) -> None:
    """**Round-2 F4 regression.**

    An earlier revision treated an unrecognized status as neutral and returned
    green. That is fail-open: an older doctor cannot know whether a newer
    engine's outcome denotes a synthesis failure, and reporting it healthy
    restores the "unknown history reads as green" hole this check exists to
    close. Warn instead — cry "I can't tell", not "wolf" and not "fine".
    """
    root, repo = _store(
        tmp_path,
        [
            {"at": "2026-07-20T10:00:00Z", "status": "ok", "node_refreshed": True},
            {"at": "2026-07-21T10:00:00Z", "status": "some-future-outcome"},
        ],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "does not recognize" in check.detail


def test_three_failures_since_success_is_an_error(tmp_path: Path) -> None:
    root, repo = _store(
        tmp_path,
        [{"at": "2026-07-20T10:00:00Z", "status": "ok"}]
        + [
            {"at": f"2026-07-2{d}T10:00:00Z", "status": "error", "error": "claude -p exited 1"}
            for d in range(4, 7)
        ],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "error"
    assert "3 failed pass" in check.detail


def test_never_succeeded_is_an_error(tmp_path: Path) -> None:
    root, repo = _store(tmp_path, [{"at": "2026-07-27T10:00:00Z", "status": "error", "error": "x"}])
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "error"
    assert "has ever refreshed the node" in check.detail


def test_only_noops_ever_is_not_an_error(tmp_path: Path) -> None:
    """An enabled project that has never had anything to curate is healthy."""
    root, repo = _store(tmp_path, [{"at": "2026-07-27T10:00:00Z", "status": "noop", "raw": 0}])
    assert diagnostics._curate_health_check(root, repo).status == "ok"


# --- R1-F4 (major): unknown history must not read as healthy ----------------


def test_unreadable_log_warns_rather_than_reporting_healthy(tmp_path: Path) -> None:
    """**Round-1 F4 regression.** An unreadable log collapsed into "no curate
    passes recorded" — restoring the all-green blindness this check prevents.

    A directory standing in for the log raises `OSError` on every platform
    (`IsADirectoryError` on POSIX, `PermissionError` on Windows), so this needs
    no monkeypatching and stays honest on the Windows leg of the matrix.
    """
    root, repo = _store(tmp_path, [])
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    log.unlink()
    log.mkdir()

    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "unreadable" in check.detail


def test_wholly_unparseable_log_warns(tmp_path: Path) -> None:
    root, repo = _store(tmp_path, [])
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    log.write_text("not json\nalso not json\n", encoding="utf-8")
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "no readable records" in check.detail


def test_torn_trailing_line_stays_fail_soft(tmp_path: Path) -> None:
    """The writer appends record-and-newline in one write, so an interrupted
    append ends **without** a trailing newline. That single torn record must not
    turn a healthy history into a warning.

    Note the fixture deliberately omits the trailing newline. An earlier version
    wrote a truncated record *followed by* `\\n`, which no single-write producer
    can emit — and under the round-4 byte-level rule that shape is correctly
    read as damage rather than a torn write.
    """
    root, repo = _store(
        tmp_path, [{"at": "2026-07-27T10:00:00Z", "status": "ok", "node_refreshed": True}]
    )
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    with log.open("a", encoding="utf-8") as fh:
        fh.write('{"at": "2026-07-27T11:00:00Z", "stat')
    assert diagnostics._curate_health_check(root, repo).status == "ok"


def test_missing_log_is_fine(tmp_path: Path) -> None:
    root, repo = _store(tmp_path, [])
    (root / "projects" / "p" / "memory" / ".curator-log.jsonl").unlink()
    assert diagnostics._curate_health_check(root, repo).status == "ok"


def test_unenabled_project_is_not_a_curate_problem(tmp_path: Path) -> None:
    root, _repo = _store(tmp_path, [{"at": "x", "status": "ok"}])
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    check = diagnostics._curate_health_check(root, elsewhere)
    assert check.status == "ok"
    assert "not an enabled project" in check.detail


# --- round-2 regressions ----------------------------------------------------


def test_planted_manifest_beside_a_foreign_binary_is_rejected(tmp_path: Path) -> None:
    """**Round-2 F1 regression.**

    A foreign script named `neurobase`, with a manifest planted in the
    conventional sibling directory, claimed every capability and passed. The
    shebang now binds the evidence: the manifest must live under the interpreter
    the script actually runs, so a planted one beside an unrelated binary is not
    believed.
    """
    root = tmp_path / "foreign"
    (root / "bin").mkdir(parents=True)
    executable = root / "bin" / "neurobase"
    # Shebang points at a system interpreter, i.e. *not* this install root.
    executable.write_text("#!/usr/bin/python3\nprint('hi')\n", encoding="utf-8")
    executable.chmod(0o755)
    package = root / "lib" / "python3.14" / "site-packages" / "neurobase"
    package.mkdir(parents=True)
    (package / capabilities.MANIFEST_NAME).write_text(
        json.dumps({"profile": capabilities.PROFILE, "provides": sorted(capabilities.PROVIDES)}),
        encoding="utf-8",
    )

    assert capabilities.provided_by(executable) == frozenset()


def test_manifest_is_accepted_when_the_shebang_matches_the_install(tmp_path: Path) -> None:
    """The counterpart: a real console script whose interpreter lives in the same
    install root is exactly what a genuine uv/venv/pipx layout looks like."""
    root = tmp_path / "genuine"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "python").write_text("", encoding="utf-8")
    executable = root / "bin" / "neurobase"
    executable.write_text(f"#!{root / 'bin' / 'python'}\n", encoding="utf-8")
    executable.chmod(0o755)
    package = root / "lib" / "python3.14" / "site-packages" / "neurobase"
    package.mkdir(parents=True)
    (package / capabilities.MANIFEST_NAME).write_text(
        json.dumps({"profile": capabilities.PROFILE, "provides": sorted(capabilities.PROVIDES)}),
        encoding="utf-8",
    )

    assert capabilities.provided_by(executable) == capabilities.PROVIDES


def test_fifo_manifest_does_not_block(tmp_path: Path) -> None:
    """**Round-2 F1 regression.** A FIFO at the manifest path made the read block
    forever, turning a read-only diagnostic into blocking IPC."""
    os = pytest.importorskip("os")
    if not hasattr(os, "mkfifo"):  # pragma: no cover - POSIX only
        pytest.skip("mkfifo unavailable")
    root = tmp_path / "fifo-install"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "python").write_text("", encoding="utf-8")
    executable = root / "bin" / "neurobase"
    # A shebang the binding *accepts*, so the read actually reaches the FIFO —
    # with `#!/bin/sh` the root check rejected first and this test passed even
    # with the bounded-read guard removed (review round 4, F4).
    executable.write_text(f"#!{root / 'bin' / 'python'}\n", encoding="utf-8")
    package = root / "lib" / "python3.14" / "site-packages" / "neurobase"
    package.mkdir(parents=True)
    os.mkfifo(package / capabilities.MANIFEST_NAME)

    assert capabilities.provided_by(executable) == frozenset()


def test_oversized_manifest_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**Round-4 F4 / round-6 F2.**

    The manifest is *valid JSON advertising the full profile*, just over the
    ceiling. Two earlier versions of this test could not fail: the first used
    65 KiB of spaces, which an unbounded reader rejects at the JSON parse; the
    second sized its padding *from* ``MAX_MANIFEST_BYTES``, so raising the
    ceiling grew the fixture too and the mutation was invisible.

    The ceiling is pinned to a small value here instead, so the fixture is fixed
    and the size check is provably the rejecting behavior.
    """
    monkeypatch.setattr(capabilities, "MAX_MANIFEST_BYTES", 512)

    root = tmp_path / "big"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "python").write_text("", encoding="utf-8")
    executable = root / "bin" / "neurobase"
    executable.write_text(f"#!{root / 'bin' / 'python'}\n", encoding="utf-8")
    package = root / "lib" / "python3.14" / "site-packages" / "neurobase"
    package.mkdir(parents=True)
    manifest = package / capabilities.MANIFEST_NAME

    payload = {
        "profile": capabilities.PROFILE,
        "provides": sorted(capabilities.PROVIDES),
        "padding": "x" * 2048,
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert manifest.stat().st_size > 512
    assert capabilities.provided_by(executable) == frozenset()

    # Control: the same valid manifest under the ceiling is accepted, so JSON
    # parsing cannot be what rejected the oversized one.
    payload["padding"] = "x"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert manifest.stat().st_size < 512
    assert capabilities.provided_by(executable) == capabilities.PROVIDES


def test_claude_project_hook_is_found_from_a_nested_cwd(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Round-2 F2 regression.**

    Claude project settings were resolved from the literal cwd, so an enabled
    repo-root hook escaped the gate whenever doctor ran from a subdirectory.
    """
    repo = tmp_path / "repo"
    nested = repo / "src" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.setattr(diagnostics.projects, "git_common_root", lambda _cwd: repo)

    stale = _stale_install(tmp_path)
    _write_claude_hook(home, stale, user=False, repo=repo)

    from_root = diagnostics._startup_hook_executables(repo)
    from_nested = diagnostics._startup_hook_executables(nested)
    assert [e for _l, e in from_nested] == [stale]
    assert from_nested == from_root
    assert [c.status for c in diagnostics._capability_checks(nested)] == ["error"]


def test_partial_still_reads_as_a_frozen_node(tmp_path: Path) -> None:
    """The converse of the two above: `partial` is where synthesis actually died."""
    root, repo = _store(
        tmp_path,
        [
            {"at": "2026-07-20T10:00:00Z", "status": "ok", "node_refreshed": True},
            {
                "at": "2026-07-21T10:00:00Z",
                "status": "partial",
                "node_refreshed": False,
                "error": "synthesis failed",
            },
        ],
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "1 failed pass" in check.detail


def test_corrupt_non_trailing_record_is_not_healthy(tmp_path: Path) -> None:
    """**Round-2 F4 regression.**

    An old success, a corrupt record, then a later append read as green — and
    that is the durable shape once a torn write is appended to, so the fail-soft
    exception for the *current* trailing line cannot cover it.
    """
    root, repo = _store(tmp_path, [])
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    log.write_text(
        json.dumps({"at": "2026-07-20T10:00:00Z", "status": "ok", "node_refreshed": True})
        + "\n{ torn mid-writ\n"
        + json.dumps({"at": "2026-07-22T10:00:00Z", "status": "noop"})
        + "\n",
        encoding="utf-8",
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "malformed" in check.detail


# --- round-3 regressions: shebang binding -----------------------------------


def test_venv_symlinked_interpreter_still_resolves(tmp_path: Path) -> None:
    """**Round-3 F1 regression — the false negative.**

    A venv's `bin/python` is normally a symlink to some base interpreter. An
    earlier revision resolved it before taking the root, which discarded the venv
    whose `site-packages` is actually imported, and reported the *real* uv-tool
    layout on the reporting machine as having no capabilities. A correct install
    must not be marked unsafe.
    """
    base = tmp_path / "base" / "bin"
    base.mkdir(parents=True)
    (base / "python3.14").write_text("", encoding="utf-8")

    root = tmp_path / "tools" / "neurobase-cli"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "python").symlink_to(base / "python3.14")
    executable = root / "bin" / "neurobase"
    executable.write_text(f"#!{root / 'bin' / 'python'}\n", encoding="utf-8")
    package = root / "lib" / "python3.14" / "site-packages" / "neurobase"
    package.mkdir(parents=True)
    (package / capabilities.MANIFEST_NAME).write_text(
        json.dumps({"profile": capabilities.PROFILE, "provides": sorted(capabilities.PROVIDES)}),
        encoding="utf-8",
    )

    assert capabilities.provided_by(executable) == capabilities.PROVIDES


def test_nested_install_using_an_outer_interpreter_is_rejected(tmp_path: Path) -> None:
    """**Round-3 F1 regression — the false positive.**

    Containment ("somewhere beneath the claimed root") certified a nested install
    whose interpreter imports from the *outer* environment. Identity is required.
    """
    outer = tmp_path / "outer"
    (outer / "bin").mkdir(parents=True)
    (outer / "bin" / "python").write_text("", encoding="utf-8")

    nested = outer / "tools" / "x"
    (nested / "bin").mkdir(parents=True)
    executable = nested / "bin" / "neurobase"
    executable.write_text(f"#!{outer / 'bin' / 'python'}\n", encoding="utf-8")
    package = nested / "lib" / "python3.14" / "site-packages" / "neurobase"
    package.mkdir(parents=True)
    (package / capabilities.MANIFEST_NAME).write_text(
        json.dumps({"profile": capabilities.PROFILE, "provides": sorted(capabilities.PROVIDES)}),
        encoding="utf-8",
    )

    assert capabilities.provided_by(executable) == frozenset()


@pytest.mark.parametrize(
    "shebang",
    ["#!/usr/bin/env python", "#!/usr/bin/env python3.14", "#!../bin/python", "#!python"],
)
def test_ambiguous_shebangs_are_refused(tmp_path: Path, shebang: str) -> None:
    """`/usr/bin/env` names the *launcher*, not the interpreter — which Python it
    picks depends on PATH at run time — and a relative shebang names no
    environment at all. Neither can be tied to a `site-packages` without running
    something, so both are refused rather than guessed."""
    root = tmp_path / f"amb{abs(hash(shebang))}"
    (root / "bin").mkdir(parents=True)
    executable = root / "bin" / "neurobase"
    executable.write_text(f"{shebang}\n", encoding="utf-8")
    package = root / "lib" / "python3.14" / "site-packages" / "neurobase"
    package.mkdir(parents=True)
    (package / capabilities.MANIFEST_NAME).write_text(
        json.dumps({"profile": capabilities.PROFILE, "provides": sorted(capabilities.PROVIDES)}),
        encoding="utf-8",
    )

    assert capabilities.provided_by(executable) == frozenset()


def test_shebangless_script_falls_back_to_convention(tmp_path: Path) -> None:
    """The documented limit, pinned so it changes deliberately: with no shebang
    (a Windows `.exe` wrapper) directory convention is the only evidence."""
    root = tmp_path / "noshebang"
    (root / "bin").mkdir(parents=True)
    executable = root / "bin" / "neurobase"
    executable.write_bytes(b"MZ\x90\x00binary wrapper")
    package = root / "lib" / "python3.14" / "site-packages" / "neurobase"
    package.mkdir(parents=True)
    (package / capabilities.MANIFEST_NAME).write_text(
        json.dumps({"profile": capabilities.PROFILE, "provides": sorted(capabilities.PROVIDES)}),
        encoding="utf-8",
    )

    assert capabilities.provided_by(executable) == capabilities.PROVIDES


# --- round-3 regressions: log states that must not read as healthy ----------


def test_existing_but_empty_log_is_not_a_fresh_install(tmp_path: Path) -> None:
    """**Round-3 F2 regression.** Only a *nonexistent* path proves no history.
    The producer appends whole records, so a zero-byte log is truncation."""
    root, repo = _store(tmp_path, [])
    (root / "projects" / "p" / "memory" / ".curator-log.jsonl").write_text("", encoding="utf-8")
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "empty" in check.detail


def test_complete_trailing_non_record_is_damage_not_a_torn_write(tmp_path: Path) -> None:
    """**Round-3 F2 regression.** A *complete* trailing JSON value that is not a
    record (`[]`) cannot be a half-written dict, so the torn-write exception
    must not cover it."""
    root, repo = _store(tmp_path, [])
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    log.write_text(
        json.dumps({"at": "2026-07-20T10:00:00Z", "status": "ok", "node_refreshed": True})
        + "\n[]\n",
        encoding="utf-8",
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "malformed" in check.detail


def test_record_missing_required_fields_cannot_prove_health(tmp_path: Path) -> None:
    """**Round-3 F2 regression.** `{"status": "ok"}` with no `at` was accepted as
    a refresh and printed `last refresh None`."""
    root, repo = _store(tmp_path, [])
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    log.write_text(json.dumps({"status": "ok"}) + "\n", encoding="utf-8")
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "None" not in check.detail


@pytest.mark.parametrize("interpreter", ["sh", "bash", "ruby", "python-not-really"])
def test_same_root_non_python_shebang_is_refused(tmp_path: Path, interpreter: str) -> None:
    """**Round-4 F1 regression.**

    The root check alone accepted any absolute interpreter whose `parent.parent`
    matched, so `#!<env>/bin/sh` beside a planted manifest was certified. The
    binding is to *the Python that imports the attesting package*; another
    interpreter says nothing about that.
    """
    root = tmp_path / f"nonpy-{interpreter}"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / interpreter).write_text("", encoding="utf-8")
    executable = root / "bin" / "neurobase"
    executable.write_text(f"#!{root / 'bin' / interpreter}\n", encoding="utf-8")
    package = root / "lib" / "python3.14" / "site-packages" / "neurobase"
    package.mkdir(parents=True)
    (package / capabilities.MANIFEST_NAME).write_text(
        json.dumps({"profile": capabilities.PROFILE, "provides": sorted(capabilities.PROVIDES)}),
        encoding="utf-8",
    )

    assert capabilities.provided_by(executable) == frozenset()


def test_complete_garbage_final_line_is_damage_not_a_torn_write(tmp_path: Path) -> None:
    """**Round-4 F2 regression.**

    Excusing "the last line failed to parse" let a *complete* garbage line read
    as healthy. The torn-append signature is a missing trailing newline, not a
    parse failure — the producer writes record and newline in one append.
    """
    root, repo = _store(tmp_path, [])
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    log.write_text(
        json.dumps({"at": "2026-07-20T10:00:00Z", "status": "ok", "node_refreshed": True})
        + "\ncomplete garbage\n",
        encoding="utf-8",
    )
    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "malformed" in check.detail


def test_truncated_utf8_in_a_torn_append_does_not_abort_doctor(tmp_path: Path) -> None:
    """**Round-4 F2 regression.** A multi-byte character cut mid-sequence raised
    `UnicodeDecodeError` out of the reader, aborting doctor rather than
    reporting. A diagnostic must degrade, never crash."""
    root, repo = _store(tmp_path, [])
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    good = json.dumps({"at": "2026-07-20T10:00:00Z", "status": "ok", "node_refreshed": True})
    # No trailing newline: an interrupted append, ending mid-UTF-8-sequence.
    log.write_bytes(good.encode() + b'\n{"at": "2026-07-21T10:00:00Z", "note": "caf\xc3')

    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "ok"  # torn tail is excused; the history is intact


def test_truncated_utf8_mid_history_is_damage(tmp_path: Path) -> None:
    """The same damage anywhere but the final append is not excusable."""
    root, repo = _store(tmp_path, [])
    log = root / "projects" / "p" / "memory" / ".curator-log.jsonl"
    good = json.dumps({"at": "2026-07-20T10:00:00Z", "status": "ok", "node_refreshed": True})
    later = json.dumps({"at": "2026-07-22T10:00:00Z", "status": "noop"})
    log.write_bytes(good.encode() + b'\n{"note": "caf\xc3\n' + later.encode() + b"\n")

    check = diagnostics._curate_health_check(root, repo)
    assert check.status == "warn"
    assert "malformed" in check.detail


@pytest.mark.parametrize(
    "interpreter",
    ["python3.13t", "python3.14t", "python3.14t.exe", "python3.14d", "python3.14td"],
)
def test_free_threaded_and_debug_abi_installs_are_accepted(
    tmp_path: Path, interpreter: str
) -> None:
    """**Round-5 F1 regression.**

    Free-threaded CPython (PEP 703, 3.13+) installs as `python3.14t`, debug
    builds as `python3.14d`. Omitting the ABI suffixes rejected genuine supported
    installs — the same false-negative class as the resolved-symlink bug, where a
    correct install is reported unsafe.
    """
    root = tmp_path / f"abi-{interpreter}"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / interpreter).write_text("", encoding="utf-8")
    executable = root / "bin" / "neurobase"
    executable.write_text(f"#!{root / 'bin' / interpreter}\n", encoding="utf-8")
    package = root / "lib" / "python3.14" / "site-packages" / "neurobase"
    package.mkdir(parents=True)
    (package / capabilities.MANIFEST_NAME).write_text(
        json.dumps({"profile": capabilities.PROFILE, "provides": sorted(capabilities.PROVIDES)}),
        encoding="utf-8",
    )

    assert capabilities.provided_by(executable) == capabilities.PROVIDES
