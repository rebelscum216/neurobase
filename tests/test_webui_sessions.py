"""Tests for the Sessions surface (app-shell plan, Phase 2b): the read-only
browser over raw captures. Covers the list (present + newest-first), the detail
page, **display-time redaction** (a secret in a raw body must never render), and
the not-found / path-traversal guards.
"""

from __future__ import annotations

from datetime import UTC, datetime
from os.path import relpath
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from neurobase.core import projects, store
from neurobase.webui.app import build_app
from neurobase.webui.routes import _capture_path

PROJECT = "myrepo"
_SECRET = "AKIAIOSFODNN7EXAMPLE"  # an AWS key shape core.redact scrubs by default


def _write_raw(root: Path, *, session_id: str, captured_at: datetime, body: str) -> Path:
    return store.write_raw(
        root,
        PROJECT,
        agent="claude",
        session_id=session_id,
        cwd="/Users/dev/myrepo",
        branch="main",
        captured_at=captured_at,
        body=body,
    )


@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    store.ensure_tree(PROJECT, root)
    projects.register_project(root, tmp_path / PROJECT, slug=PROJECT)
    _write_raw(
        root,
        session_id="oldsess1",
        captured_at=datetime(2026, 7, 1, 9, 0, 0, tzinfo=UTC),
        body="## Session\n- agent: claude\n\nolder capture",
    )
    _write_raw(
        root,
        session_id="newsess2",
        captured_at=datetime(2026, 7, 2, 9, 0, 0, tzinfo=UTC),
        body=f"## Session\n\nnewer capture with a secret {_SECRET} in it",
    )
    return root


@pytest.fixture
def client(store_root: Path) -> TestClient:
    app: Starlette = build_app(store_root)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_sessions_nav_is_a_live_link_not_a_stub(client: TestClient) -> None:
    html = client.get("/suggestions").text
    assert '<a class="navitem" href="/sessions"' in html or 'href="/sessions"' in html
    # the rail count for sessions reflects the two planted captures
    assert '<span class="count">2</span>' in html


def test_sessions_list_shows_captures_newest_first(client: TestClient) -> None:
    r = client.get("/sessions")
    assert r.status_code == 200
    html = r.text
    assert "newsess2" in html and "oldsess1" in html
    # newest capture first: newsess2 must appear before oldsess1 in the table
    assert html.index("newsess2") < html.index("oldsess1")


def test_session_detail_renders_metadata_and_body(client: TestClient, store_root: Path) -> None:
    doc = store.list_raw(store_root, PROJECT, unconsumed_only=False)[0]
    r = client.get(f"/sessions/{PROJECT}/{doc.file_path.name}")
    assert r.status_code == 200
    assert "Capture metadata" in r.text
    assert "Transcript" in r.text


def test_session_detail_redacts_secrets_at_display(client: TestClient, store_root: Path) -> None:
    """The secret is in the planted raw body; the detail page must never echo it,
    even though the body was already scrubbed at capture (defense-in-depth §14)."""
    secret_raw = next(
        d for d in store.list_raw(store_root, PROJECT, unconsumed_only=False) if _SECRET in d.body
    )
    r = client.get(f"/sessions/{PROJECT}/{secret_raw.file_path.name}")
    assert r.status_code == 200
    assert _SECRET not in r.text, "a secret rendered unredacted on the session page"


def test_unknown_project_is_404(client: TestClient) -> None:
    assert client.get("/sessions/no-such-project/anything.md").status_code == 404


def test_unknown_capture_file_is_404(client: TestClient) -> None:
    assert client.get(f"/sessions/{PROJECT}/does-not-exist.md").status_code == 404


def test_capture_path_rejects_escapes_and_subdirs(store_root: Path) -> None:
    """The containment guard directly (the router alone can't express a real
    escape through a single ``{file}`` segment). A crafted ``..`` name must not
    resolve to a file outside the project's ``raw/`` — e.g. the store's
    ``store.toml``, which really exists — and a subdir dip is refused too."""
    raw_dir = (store.memory_dir(PROJECT, store_root) / "raw").resolve()
    good = store.list_raw(store_root, PROJECT, unconsumed_only=False)[0].file_path.name
    assert _capture_path(raw_dir, good, store_root) is not None, "a real capture must resolve"

    escape_target = store_root.resolve() / "store.toml"
    assert escape_target.is_file(), "precondition: the escape target really exists"
    # both an absolute path and a correctly-counted ../ chain reach store.toml —
    # the guard must refuse both even though the target is a real file.
    rel_escape = relpath(escape_target, raw_dir)
    assert ".." in rel_escape and (raw_dir / rel_escape).resolve() == escape_target
    assert _capture_path(raw_dir, rel_escape, store_root) is None, "escaped raw/ via .."
    assert _capture_path(raw_dir, str(escape_target), store_root) is None, "absolute escape"
    assert _capture_path(raw_dir, "..", store_root) is None
    assert _capture_path(raw_dir, "nested/deeper.md", store_root) is None, "subdir dip"


def test_capture_path_rejects_a_symlinked_raw_dir(tmp_path: Path, store_root: Path) -> None:
    """P2-SAFETY-SECURITY-003: if the project's ``raw/`` is itself a symlink to an
    external directory, a file sitting *directly* inside that resolved directory
    still passes the immediate-parent check — but it is outside the validated store
    and must be refused by the store-root containment guard."""
    external = tmp_path / "external"
    external.mkdir()
    (external / "secret.md").write_text(
        "---\nsession_id: x\n---\n\nexternal loot", encoding="utf-8"
    )
    real_raw = store.memory_dir(PROJECT, store_root) / "raw"
    # Replace the real raw/ with a symlink to the external dir.
    for f in real_raw.iterdir():
        f.unlink()
    real_raw.rmdir()
    real_raw.symlink_to(external, target_is_directory=True)

    raw_dir = real_raw.resolve()  # resolves to `external`, outside the store
    assert _capture_path(raw_dir, "secret.md", store_root) is None, "symlinked raw/ must be refused"


def test_symlinked_raw_dir_is_404_over_http(tmp_path: Path, store_root: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    (external / "secret.md").write_text(
        f"---\nsession_id: x\n---\n\nexternal loot {_SECRET}", encoding="utf-8"
    )
    real_raw = store.memory_dir(PROJECT, store_root) / "raw"
    for f in real_raw.iterdir():
        f.unlink()
    real_raw.rmdir()
    real_raw.symlink_to(external, target_is_directory=True)

    client = TestClient(build_app(store_root), base_url="http://127.0.0.1:8765")
    r = client.get(f"/sessions/{PROJECT}/secret.md")
    assert r.status_code == 404
    assert "external loot" not in r.text


def test_symlinked_raw_dir_not_in_sessions_list(tmp_path: Path, store_root: Path) -> None:
    """P2-SAFETY-SECURITY-003 (round 2): the LIST read must also refuse a symlinked
    `raw/` — a well-formed external capture would otherwise render its metadata on
    /sessions even though the detail read refuses it."""
    external = tmp_path / "external"
    external.mkdir()
    (external / "2026-07-09T00-00-00.000000Z_claude_extsess1.md").write_text(
        "---\nsession_id: EXTERNALSESS\nagent: claude\nbranch: leak\n"
        "captured_at: 2026-07-09T00:00:00Z\n---\n\nexternal capture body",
        encoding="utf-8",
    )
    real_raw = store.memory_dir(PROJECT, store_root) / "raw"
    for f in real_raw.iterdir():
        f.unlink()
    real_raw.rmdir()
    real_raw.symlink_to(external, target_is_directory=True)

    client = TestClient(build_app(store_root), base_url="http://127.0.0.1:8765")
    html = client.get("/sessions").text
    assert "EXTERNALSESS" not in html, "a symlinked raw/ dir surfaced on the sessions list"


def test_traversal_url_is_404_over_http(client: TestClient) -> None:
    r = client.get(f"/sessions/{PROJECT}/..%2f..%2f..%2fstore.toml")
    assert r.status_code == 404


# --- two-pane master/detail (Phase 2b restyle) ------------------------------


def _first_file(store_root: Path) -> str:
    return store.list_raw(store_root, PROJECT, unconsumed_only=False)[0].file_path.name


def test_sessions_is_two_pane_with_a_placeholder(client: TestClient) -> None:
    html = client.get("/sessions").text
    assert 'class="split"' in html, "the Sessions surface should render the two-pane split"
    assert "Select a capture" in html, "the empty detail pane should prompt a selection"


def test_clicking_a_session_previews_it_inline(client: TestClient, store_root: Path) -> None:
    file = _first_file(store_root)
    html = client.get(f"/sessions/{PROJECT}/{file}").text
    # the list stays on the left AND the detail shows in the right pane
    assert 'class="split"' in html
    assert "Capture metadata" in html and "Transcript" in html
    # with an affordance to open the capture as its own standalone page
    assert f"/sessions/{PROJECT}/{file}?view=full" in html


def test_selected_row_is_highlighted(client: TestClient, store_root: Path) -> None:
    file = _first_file(store_root)
    assert 'class="on"' in client.get(f"/sessions/{PROJECT}/{file}").text
    # nothing is highlighted when no capture is selected
    assert 'class="on"' not in client.get("/sessions").text


def test_fragment_returns_just_the_pane(client: TestClient, store_root: Path) -> None:
    """The ?fragment=1 response (what the inline JS fetches to swap the pane
    without a reload) is the pane content only — no page shell, no list."""
    file = _first_file(store_root)
    html = client.get(f"/sessions/{PROJECT}/{file}", params={"fragment": "1"}).text
    assert "Capture metadata" in html  # the pane's detail
    assert f"/sessions/{PROJECT}/{file}?view=full" in html  # the open-as-page link
    assert "<html" not in html.lower(), "fragment must not carry the full page shell"
    assert 'class="split"' not in html, "fragment must be the pane only, not the list"


def test_open_as_page_is_the_standalone_view(client: TestClient, store_root: Path) -> None:
    file = _first_file(store_root)
    html = client.get(f"/sessions/{PROJECT}/{file}", params={"view": "full"}).text
    assert "Capture metadata" in html  # the detail is present
    assert 'class="split"' not in html  # but NOT the two-pane list
    assert "page narrow" in html  # it renders as the standalone narrow page
