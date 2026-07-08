"""The Phase 5 MVP milestone (build-plan §5 "Done when"): a Claude raw and a
Codex raw fold into ONE fact set, and BOTH next-sessions receive the node."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from neurobase.adapters.claude import recall as claude_recall
from neurobase.adapters.claude import scribe as claude_scribe
from neurobase.adapters.codex import recall as codex_recall
from neurobase.adapters.codex import scribe as codex_scribe
from neurobase.cli import app
from neurobase.core import projects, store
from neurobase.curator import engine

runner = CliRunner()


class FakeBrain:
    name = "fake"

    def __init__(self, plan: dict[str, Any], node_text: str) -> None:
        self._plan = plan
        self._node_text = node_text

    def plan_json(self, system: str, user: str) -> dict:
        return self._plan

    def text(self, system: str, user: str) -> str:
        return self._node_text


@pytest.fixture(autouse=True)
def _no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_recall, "spawn_curate_if_stale", lambda root, cwd: None)
    monkeypatch.setattr(codex_recall, "spawn_curate_if_stale", lambda root, cwd: None)


def test_claude_plus_codex_fold_and_both_sessions_recall(tmp_path: Path) -> None:
    root = tmp_path / "store"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    projects.register_project(root, repo, slug="myrepo")
    store.ensure_tree("myrepo", root)

    # 1) A Claude session captures a fact (via the real Claude scribe).
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "isSidechain": False,
                "cwd": str(repo),
                "gitBranch": "main",
                "sessionId": "c1",
                "message": {"role": "user", "content": "remember: this project uses ruff"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "assistant",
                "isSidechain": False,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "noted"}]},
            }
        ),
        encoding="utf-8",
    )
    claude_raw = claude_scribe.scribe(
        root, transcript_path=transcript, cwd=str(repo), reason="clear", session_id="c1"
    )

    # 2) A Codex session captures a different fact (via the real Codex scribe).
    rollout = tmp_path / "rollout-1.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "type": "session_meta",
                    "payload": {
                        "session_id": "x1",
                        "timestamp": "2026-07-05T23:21:06Z",
                        "cwd": str(repo),
                        "git": {"branch": "main"},
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "deploy via fly.io"},
                },
                {"type": "event_msg", "payload": {"type": "agent_message", "message": "ok"}},
            ]
        ),
        encoding="utf-8",
    )
    codex_raw = codex_scribe.scribe(root, rollout_path=rollout)

    assert claude_raw is not None and codex_raw is not None
    raws = store.list_raw(root, "myrepo", unconsumed_only=False)
    assert {d.get("agent") for d in raws} == {"claude", "codex"}

    # 3) Curate folds BOTH agents' raws into one fact set (one node).
    plan = {
        "upserts": [
            {
                "slug": "project-conventions",
                "body": "Uses ruff; deploys via fly.io.",
                "supersedes": [],
                "from_raw": [claude_raw.name, codex_raw.name],
            }
        ],
        "tombstones": [],
    }
    node_body = "# Project status\n\nUses ruff. Deploys via fly.io."
    summary = engine.curate(root, "myrepo", FakeBrain(plan, node_body))
    assert summary["upserts"] == 1

    # Provenance spans both agents; both raws consumed; one node exists.
    fact = store.read_doc(store.memory_dir("myrepo", root) / "curated" / "project-conventions.md")
    assert sorted(fact["provenance"]) == sorted([f"raw/{claude_raw.name}", f"raw/{codex_raw.name}"])
    assert all(d.get("consumed") for d in store.list_raw(root, "myrepo", unconsumed_only=False))
    nodes = list((store.memory_dir("myrepo", root) / "nodes").glob("*.md"))
    assert len(nodes) == 1

    # 4) BOTH next-sessions (Claude and Codex) receive the node.
    payload = json.dumps({"cwd": str(repo)})
    for agent in ("claude", "codex"):
        result = runner.invoke(
            app, ["hook", agent, "session-start", "--root", str(root)], input=payload
        )
        assert result.exit_code == 0, agent
        out = json.loads(result.output.strip())
        assert "Deploys via fly.io." in out["hookSpecificOutput"]["additionalContext"], agent


def test_codex_raw_alone_curates(tmp_path: Path) -> None:
    """A Codex-only session still produces a fact the next session recalls
    (the demo direction: teach Codex, Claude's next session knows it)."""
    root = tmp_path / "store"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    projects.register_project(root, repo, slug="myrepo")
    store.ensure_tree("myrepo", root)

    rollout = tmp_path / "rollout-1.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "type": "session_meta",
                    "payload": {
                        "session_id": "x1",
                        "timestamp": "2026-07-05T23:21:06Z",
                        "cwd": str(repo),
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "use pnpm not npm"},
                },
            ]
        ),
        encoding="utf-8",
    )
    raw = codex_scribe.scribe(root, rollout_path=rollout)
    assert raw is not None

    plan = {
        "upserts": [
            {
                "slug": "pkg-mgr",
                "body": "Use pnpm, not npm.",
                "supersedes": [],
                "from_raw": [raw.name],
            }
        ],
        "tombstones": [],
    }
    engine.curate(root, "myrepo", FakeBrain(plan, "# Status\n\nUse pnpm, not npm."))

    # Claude's next session recalls the Codex-taught fact.
    result = runner.invoke(
        app,
        ["hook", "claude", "session-start", "--root", str(root)],
        input=json.dumps({"cwd": str(repo)}),
    )
    assert result.exit_code == 0
    out = json.loads(result.output.strip())
    assert "Use pnpm, not npm." in out["hookSpecificOutput"]["additionalContext"]
