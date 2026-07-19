"""Stdio-transport smoke test for the shipped MCP entrypoint (coverage report Gap 4).

``tests/test_mcp_server.py`` drives the handlers **in-process**. Nothing there
proves the server actually speaks MCP over the stdio transport it ships on, nor
that ``neurobase mcp serve`` — the exact command every MCP client spawns — works
end to end. A regression in the entrypoint (wrong transport, an import that only
blows up under ``python -m``, a CLI wiring change) would leave the whole suite
green while every client failed to connect.

Scope is deliberately narrow: the transport + entrypoint seam, and nothing else.
Handler semantics stay in ``test_mcp_server.py``; this file does not re-test them.

**Why every wait here is bounded.** The repo runs a 60s pytest-timeout guard,
added after a documented runaway incident, and "subprocess test blocks forever on
a pipe read" is precisely the failure that guard exists to catch. So: the child is
always spawned inside a context manager that terminates it even when an assertion
fails, and every read/wait carries its own timeout that trips well before the
suite guard — a broken server fails this file loudly instead of hanging CI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from neurobase.core import projects, store
from neurobase.mcp.server import serve

# Generous enough for a cold interpreter start on a loaded Windows runner (a warm
# local start is ~0.3s), but far enough under the suite's 60s pytest-timeout that
# a genuine hang is reported by *this* test, naming the seam that broke, rather
# than by the global guard.
_TIMEOUT_SECONDS = 30.0

# Distinctive marker so the tools/call assertion can only pass if the payload
# really travelled out of the temp store, through the server, over the pipe.
_NODE_BODY = "stdio-smoke marker: alpha ships on uv."

_EXPECTED_TOOLS = [
    "memory_list_projects",
    "memory_read_node",
    "memory_remember",
    "memory_search",
    "recommendations_list",
]


# --- fixtures / helpers --------------------------------------------------


def _build_store(tmp_path: Path) -> Path:
    """A real on-disk store with one registered project and one status node —
    built the same way ``test_mcp_server.py`` builds its fixtures, so the child
    reads a store shaped exactly like production."""
    root = tmp_path / "store-root"
    store.ensure_tree("alpha", root)
    projects.register_project(root, tmp_path / "alpha", slug="alpha")
    store.write_node(root, "alpha", "alpha-status", _NODE_BODY)
    return root


def _home_overrides(home: Path) -> dict[str, str]:
    """Env that repoints ``config.config_path()`` at a throwaway home.

    Without this the server loads the *developer's* ``~/.config/neurobase/
    config.toml`` (``%APPDATA%`` on Windows), which can flip ``[mcp]
    expose_resources`` and change the surface this test asserts on. Covers the
    POSIX and Windows lookups both, since ``Path.home()`` reads ``USERPROFILE``
    on Windows and ``HOME`` elsewhere.
    """
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
    }


def _child_env(tmp_path: Path, root: Path) -> dict[str, str]:
    """Explicit env for the child process. Inherits the ambient env (Windows
    needs ``SYSTEMROOT``/``PATH`` just to start Python) but strips every
    ``NEUROBASE_*`` var first, so a developer's exported ``NEUROBASE_ROOT`` can
    never point the child at their real store."""
    home = tmp_path / "child-home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEUROBASE_")}
    env.update(_home_overrides(home))
    # Backstop for the explicit --root flag: if the CLI ever stops threading the
    # flag through, resolve_root falls back to this rather than to ~/neurobase.
    env["NEUROBASE_ROOT"] = str(root)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _serve_argv(root: Path) -> list[str]:
    """Launch through ``sys.executable -m`` — a bare ``neurobase`` console script
    is not guaranteed to be on PATH in the test environment."""
    return [sys.executable, "-m", "neurobase", "mcp", "serve", "--root", str(root)]


# --- the protocol round trip ---------------------------------------------


async def _round_trip(root: Path, cwd: Path, env: dict[str, str]) -> tuple[str, list[str], dict]:
    """Full client-side handshake against the real subprocess. ``stdio_client``
    owns process teardown (close stdin → wait → SIGTERM → SIGKILL) and runs it in
    a ``finally``, so an assertion failure inside the block still reaps the
    child."""
    argv = _serve_argv(root)
    params = StdioServerParameters(command=argv[0], args=argv[1:], env=env, cwd=str(cwd))
    timeout = timedelta(seconds=_TIMEOUT_SECONDS)
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream, read_timeout_seconds=timeout) as session,
    ):
        init = await session.initialize()
        tools = await session.list_tools()
        called = await session.call_tool(
            "memory_read_node", {"project": "alpha", "name": "alpha-status"}
        )
        assert not called.isError, called.content
        block = called.content[0]
        assert isinstance(block, types.TextContent)
        payload: dict = json.loads(block.text)
        return init.serverInfo.name, sorted(t.name for t in tools.tools), payload


def test_stdio_handshake_lists_tools_and_round_trips_a_call(tmp_path: Path) -> None:
    """The shipped entrypoint really speaks MCP: a client that knows nothing but
    the protocol can initialize, discover the five baseline tools, and get a
    payload back that provably came out of the temp store on disk."""
    root = _build_store(tmp_path)
    cwd = tmp_path / "alpha"
    cwd.mkdir(exist_ok=True)

    async def _run() -> tuple[str, list[str], dict]:
        with anyio.fail_after(_TIMEOUT_SECONDS):
            return await _round_trip(root, cwd, _child_env(tmp_path, root))

    server_name, tool_names, payload = anyio.run(_run)

    assert server_name == "neurobase"
    assert tool_names == _EXPECTED_TOOLS
    assert payload["found"] is True
    assert payload["body"] == _NODE_BODY


# --- clean shutdown ------------------------------------------------------


def _initialize_line() -> bytes:
    """One hand-framed ``initialize`` request. Hand-framed on purpose: this test
    needs the child's **exit code**, which ``stdio_client`` reaps internally and
    never exposes, and ``communicate()`` gives us exit code, both streams and a
    single enclosing timeout in one call."""
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": types.LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "neurobase-stdio-smoke", "version": "0"},
        },
    }
    return json.dumps(request).encode("utf-8") + b"\n"


def test_serve_answers_then_exits_zero_when_the_client_closes_stdin(tmp_path: Path) -> None:
    """A stdio server that outlives its client is a leaked process, and one that
    dies non-zero makes clients report a crash. Assert the documented shutdown:
    answer the request, then exit 0 on stdin EOF, with a clean stderr."""
    root = _build_store(tmp_path)
    cwd = tmp_path / "alpha"
    cwd.mkdir(exist_ok=True)
    proc = subprocess.Popen(
        _serve_argv(root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_env(tmp_path, root),
        cwd=str(cwd),
    )
    try:
        # communicate() writes, closes stdin, drains both pipes and waits — all
        # under one timeout, so no step can block indefinitely.
        stdout, stderr = proc.communicate(input=_initialize_line(), timeout=_TIMEOUT_SECONDS)
    finally:
        if proc.poll() is None:  # pragma: no cover - only on a hung/failed child
            proc.kill()
            proc.communicate()

    assert proc.returncode == 0, stderr.decode("utf-8", "replace")
    # Byte-level checks: the server writes through a text wrapper, so lines
    # arrive CRLF-terminated on Windows and a str/splitlines assumption would
    # be platform-dependent.
    assert b'"serverInfo"' in stdout, "server exited without answering initialize"
    assert b"Traceback" not in stderr, stderr.decode("utf-8", "replace")


# --- the entrypoint itself -----------------------------------------------


def test_serve_selects_the_stdio_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``serve`` is one line, but it is the line that decides which transport the
    shipped server speaks; switching it to anything else silently breaks every
    stdio client. Covered in-process because the subprocess tests above run in a
    child that the coverage run does not measure."""
    for key, value in _home_overrides(tmp_path / "home").items():
        monkeypatch.setenv(key, value)
    transports: list[str] = []
    monkeypatch.setattr(
        "mcp.server.fastmcp.FastMCP.run",
        lambda self, transport="stdio", **kwargs: transports.append(transport),
    )

    serve(_build_store(tmp_path))

    assert transports == ["stdio"]
