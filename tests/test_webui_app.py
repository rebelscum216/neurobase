"""Tests for the Web UI Phase 1 app skeleton (build_app wiring, ``GET /``'s
redirect to the Suggestions list, and the same-origin/CSRF middleware).

The real Suggestions routes (list/detail/accept/reject/edit) are covered in
``tests/test_webui_suggestions.py``; this file only proves the server,
routing, and security middleware run end to end — see
``docs/notes/2026-07-15-webui-phase1-plan.md``.
"""

from __future__ import annotations

from pathlib import Path

import anyio
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

from neurobase.webui.app import build_app
from neurobase.webui.security import check_same_origin_csrf, is_loopback_host


@pytest.fixture
def app(tmp_path: Path) -> Starlette:
    return build_app(tmp_path)


@pytest.fixture
def client(app: Starlette) -> TestClient:
    # base_url pins the Host header to a loopback authority — TestClient's
    # default `testserver` authority is (correctly) rejected by the §14 gate.
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_get_root_redirects_to_suggestions(client: TestClient) -> None:
    # Web UI Phase 1 "Routes" table: `GET /` -> redirect to `/suggestions`.
    # follow_redirects=False so this test asserts the redirect itself, not
    # (also) the destination page's content — that belongs to
    # test_webui_suggestions.py.
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/suggestions"


def test_get_root_follows_through_to_a_real_page(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Neurobase UI" in response.text


def test_post_without_csrf_token_is_rejected(client: TestClient) -> None:
    # No mutating routes exist yet (next stage lands Suggestions POSTs) — the
    # CSRF middleware runs before routing, so even a nonexistent path is
    # intercepted with 403, not 404, proving the check fires unconditionally
    # for every POST.
    response = client.post("/suggestions/example/accept", data={})
    assert response.status_code == 403


def test_post_with_mismatched_origin_is_rejected_even_with_correct_token(
    app: Starlette, client: TestClient
) -> None:
    response = client.post(
        "/suggestions/example/accept",
        data={"csrf_token": app.state.csrf_token},
        headers={"origin": "http://evil.example"},
    )
    assert response.status_code == 403


def test_post_with_matching_origin_and_token_reaches_routing(
    app: Starlette, client: TestClient
) -> None:
    # Proves the middleware isn't rejecting everything unconditionally: a
    # same-origin, correctly-tokened POST reaches normal routing and 404s
    # (no such route exists yet) instead of being blocked at 403.
    response = client.post(
        "/suggestions/example/accept",
        data={"csrf_token": app.state.csrf_token},
        headers={"origin": str(client.base_url)},
    )
    assert response.status_code == 404


# --- §14 loopback-Host gate (DNS-rebinding defense) --------------------------


def test_get_with_non_loopback_host_is_rejected(client: TestClient) -> None:
    # The gate covers EVERY method, not just POSTs: a rebound hostname must
    # not be able to read pages (or the CSRF token embedded in their forms).
    response = client.get("/suggestions", headers={"host": "evil.example:8765"})
    assert response.status_code == 403
    assert "non-loopback host" in response.text


def test_post_with_matching_but_non_loopback_host_and_origin_is_rejected(
    app: Starlette, client: TestClient
) -> None:
    # The DNS-rebinding shape: Host and Origin AGREE (both the attacker's
    # hostname, resolved to 127.0.0.1) and the token is correct — the old
    # origin==host check alone would have passed this through to routing.
    response = client.post(
        "/suggestions/example/accept",
        data={"csrf_token": app.state.csrf_token},
        headers={"host": "evil.example:8765", "origin": "http://evil.example:8765"},
    )
    assert response.status_code == 403
    assert "non-loopback host" in response.text


def test_localhost_host_is_accepted(client: TestClient) -> None:
    response = client.get("/", headers={"host": "localhost:8765"}, follow_redirects=False)
    assert response.status_code in (302, 307)


def test_is_loopback_host_vocabulary() -> None:
    assert is_loopback_host("127.0.0.1:8765")
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("localhost:9000")
    assert is_loopback_host("[::1]:8765")
    assert not is_loopback_host("evil.example:8765")
    assert not is_loopback_host("127.0.0.1.evil.example")
    assert not is_loopback_host("")
    assert not is_loopback_host(None)


def test_check_same_origin_csrf_rejects_non_loopback_host() -> None:
    request = _post_request(
        {
            "host": "evil.example:8765",
            "origin": "http://evil.example:8765",
            "content-type": "application/x-www-form-urlencoded",
        },
        body=b"csrf_token=the-token",
    )
    result = anyio.run(check_same_origin_csrf, request, "the-token")
    assert result == "non-loopback host rejected"


# --- serve()/CLI contract (§14: bind + schema gate) ---------------------------


def test_serve_binds_loopback_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import uvicorn

    from neurobase.webui.app import serve

    captured: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    serve(tmp_path, port=4321)
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 4321


def test_ui_command_refuses_newer_store_schema_before_serving(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # §14/§10: `neurobase ui` runs the D11 schema check before serving —
    # a newer-schema store must exit 1 without the server ever starting.
    from typer.testing import CliRunner

    import neurobase.webui.app as webui_app
    from neurobase.cli import app as cli_app

    (tmp_path / "store.toml").write_text(
        'schema = 999\ncreated_at = "2026-07-16T00:00:00Z"\n', encoding="utf-8"
    )

    def must_not_serve(*args: object, **kwargs: object) -> None:
        raise AssertionError("serve() must not be reached on a newer-schema store")

    monkeypatch.setattr(webui_app, "serve", must_not_serve)
    result = CliRunner().invoke(cli_app, ["ui", "--root", str(tmp_path)])
    assert result.exit_code == 1


# --- direct unit tests of the CSRF/origin-check function itself ------------


def _post_request(headers: dict[str, str], body: bytes = b"") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/suggestions/example/accept",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def test_check_same_origin_csrf_rejects_missing_origin_and_referer() -> None:
    request = _post_request({"host": "127.0.0.1:8765"})
    result = anyio.run(check_same_origin_csrf, request, "the-token")
    assert result == "cross-origin request rejected"


def test_check_same_origin_csrf_rejects_wrong_token() -> None:
    request = _post_request(
        {
            "host": "127.0.0.1:8765",
            "origin": "http://127.0.0.1:8765",
            "content-type": "application/x-www-form-urlencoded",
        },
        body=b"csrf_token=not-the-token",
    )
    result = anyio.run(check_same_origin_csrf, request, "the-token")
    assert result == "missing or invalid csrf_token"


def test_check_same_origin_csrf_accepts_matching_origin_and_token() -> None:
    request = _post_request(
        {
            "host": "127.0.0.1:8765",
            "origin": "http://127.0.0.1:8765",
            "content-type": "application/x-www-form-urlencoded",
        },
        body=b"csrf_token=the-token",
    )
    result = anyio.run(check_same_origin_csrf, request, "the-token")
    assert result is None


def test_check_same_origin_csrf_falls_back_to_referer() -> None:
    request = _post_request(
        {
            "host": "127.0.0.1:8765",
            "referer": "http://127.0.0.1:8765/suggestions/example/accept",
            "content-type": "application/x-www-form-urlencoded",
        },
        body=b"csrf_token=the-token",
    )
    result = anyio.run(check_same_origin_csrf, request, "the-token")
    assert result is None
