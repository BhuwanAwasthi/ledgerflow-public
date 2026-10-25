"""The login wall (N2): default-deny on every route, Supabase JWT verification (JWKS and legacy HS256), the
AUTH_MODE=dev guards, the SSE stream and file tokens, and tokens kept out of the logs."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.agents.base import StubScript
from app.auth import JWTVerifier, check_dev_mode
from app.log_redaction import RedactTokens, redact
from app.main import create_app
from app.models import StreamToken, User, Vendor, utcnow
from app.settings import Settings
from app.tenancy import bind_owner
from app.tokens import FileTokens
from tests.conftest import ALICE, BOB, ISSUER, auth_client, auth_headers, make_token
from tests.test_api import parse_sse, wait_status

PUBLIC = {("GET", "/healthz")}


def _fill(path: str) -> str:
    """A concrete URL for an OpenAPI path template."""
    return (path.replace("{case_id}", "1").replace("{doc_id}", "1").replace("{page}", "1")
            .replace("{version}", "1").replace("{batch_id}", "1").replace("{vendor_id}", "1")
            .replace("{po_id}", "1").replace("{notification_id}", "1").replace("{token}", "tok")
            .replace("{scenario_id}", "S01_happy").replace("{filename}", "f.pdf").replace("{entity}", "vendors")
            .replace("{path}", "rules.yaml"))


def _routes(app: Any) -> list[tuple[str, str]]:
    out = []
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            out.append((method.upper(), path))
    return out


def test_every_route_needs_a_token_except_healthz(test_settings: Settings):
    app = create_app(test_settings, script=StubScript())
    routes = _routes(app)
    assert ("GET", "/api/cases/{case_id}") in routes and ("POST", "/api/me/reset-workspace") in routes
    assert len(routes) > 40
    with TestClient(app) as anon:
        for method, path in routes:
            r = anon.request(method, _fill(path))
            if (method, path) in PUBLIC:
                assert r.status_code == 200, (method, path)
            else:
                assert r.status_code == 401, (method, path, r.status_code, r.text)
                assert r.headers["www-authenticate"] == "Bearer"
        # the schema and the docs are not served outside dev mode; the dev login does not exist
        assert anon.get("/openapi.json").status_code == 404 and anon.get("/docs").status_code == 404
        assert anon.post("/api/auth/dev-login", json={"email": "a@b.co"}).status_code in (404, 405)
        health = anon.get("/healthz").json()
        assert health["auth_mode"] == "supabase"


@pytest.mark.parametrize("make_bad", [
    lambda: make_token(ALICE, ttl=-120),  # expired (beyond the 30 s leeway)
    lambda: make_token(ALICE, secret="not-the-project-secret-0123456789abcdef"),  # forged
    lambda: make_token(ALICE, aud="anon"),
    lambda: make_token(ALICE, iss="https://evil.example/auth/v1"),
    lambda: make_token(ALICE, role="anon"),
    lambda: jwt.encode({"sub": ALICE.id, "aud": "authenticated", "exp": int(time.time()) + 60}, None, algorithm="none"),
    lambda: "not.a.jwt",
])
def test_invalid_tokens_are_401(test_settings: Settings, make_bad):
    with TestClient(create_app(test_settings, script=StubScript())) as client:
        r = client.get("/api/me", headers={"Authorization": f"Bearer {make_bad()}"})
        assert r.status_code == 401, r.text
        assert client.get("/api/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_first_sign_in_creates_the_user_and_seeds_the_workspace(test_settings: Settings):
    with auth_client(create_app(test_settings, script=StubScript())) as client:
        me = client.get("/api/me")
        assert me.status_code == 200, me.text
        body = me.json()
        assert (body["id"], body["email"], body["name"], body["mode"]) == (ALICE.id, ALICE.email, ALICE.name, "unset")
        assert body["baseline_version"] == body["baseline_current"] and body["baseline_outdated"] is False
        assert len(client.get("/api/vendors").json()) == 7
        client.get("/api/me")  # a second request seeds nothing more
        sf = client.app.state.session_factory
        with bind_owner(sf, ALICE.id)() as s:
            assert s.scalar(select(func.count()).select_from(Vendor)) == 7
        with sf() as s:
            assert s.scalar(select(func.count()).select_from(User)) == 1


# ---------------------------------------------------------------- JWKS (asymmetric keys)
@pytest.fixture(scope="module")
def ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _jwks(key: ec.EllipticCurvePrivateKey, kid: str) -> dict[str, Any]:
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": kid, "alg": "ES256", "use": "sig"}]}


def _es256(key: ec.EllipticCurvePrivateKey, kid: str, **claims: Any) -> str:
    now = int(time.time())
    payload = {"sub": ALICE.id, "email": ALICE.email, "aud": "authenticated", "role": "authenticated",
               "iss": ISSUER, "iat": now, "exp": now + 600, **claims}
    return jwt.encode(payload, key, algorithm="ES256", headers={"kid": kid})


def test_jwks_verification(ec_key):
    fetches: list[int] = []

    def fetch() -> dict[str, Any]:
        fetches.append(1)
        return _jwks(ec_key, "k1")

    v = JWTVerifier(jwks_url="https://test-project.supabase.co/auth/v1/.well-known/jwks.json", issuer=ISSUER,
                    fetch_jwks=fetch)
    assert v.verify(_es256(ec_key, "k1")).id == ALICE.id
    assert v.verify(_es256(ec_key, "k1")).email == ALICE.email
    assert len(fetches) == 1  # the key set is cached

    from app.auth import AuthError

    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(AuthError):
        v.verify(_es256(other, "k1"))  # right kid, wrong key
    with pytest.raises(AuthError):
        v.verify(_es256(ec_key, "k1", exp=int(time.time()) - 300))
    with pytest.raises(AuthError, match="unknown signing key"):
        v.verify(_es256(ec_key, "k2"))
    # no HS256 secret configured: an HS256 token (e.g. signed with the public key as a secret) is refused
    with pytest.raises(AuthError):
        v.verify(make_token(ALICE))


def test_jwks_app_end_to_end(test_settings: Settings, ec_key):
    settings = test_settings.model_copy(update={"supabase_jwt_secret": ""})
    verifier = JWTVerifier(jwks_url="x", issuer=ISSUER, fetch_jwks=lambda: _jwks(ec_key, "k1"))
    with TestClient(create_app(settings, script=StubScript(), verifier=verifier)) as client:
        ok = client.get("/api/me", headers={"Authorization": f"Bearer {_es256(ec_key, 'k1')}"})
        assert ok.status_code == 200 and ok.json()["id"] == ALICE.id
        assert client.get("/api/me", headers=auth_headers()).status_code == 401  # HS256 not configured here


# ---------------------------------------------------------------- AUTH_MODE=dev
def test_dev_mode_refuses_production_settings(test_settings: Settings):
    dev = test_settings.model_copy(update={"auth_mode": "dev"})
    check_dev_mode(dev)  # SQLite + local storage: fine
    for bad in (
        {"storage_backend": "supabase"},
        {"database_url": "postgresql://u:p@db.example:5432/postgres"},
        {"render": "true"},
    ):
        with pytest.raises(RuntimeError, match="AUTH_MODE=dev refuses to start"):
            create_app(dev.model_copy(update=bad))
    with pytest.raises(RuntimeError, match="must be 'supabase' or 'dev'"):
        check_dev_mode(test_settings.model_copy(update={"auth_mode": "none"}))


def test_dev_mode_refuses_when_render_env_is_set(test_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(RuntimeError, match="RENDER is set"):
        check_dev_mode(test_settings.model_copy(update={"auth_mode": "dev"}))


def test_dev_login(test_settings: Settings):
    dev = test_settings.model_copy(update={"auth_mode": "dev", "supabase_jwt_secret": "", "supabase_url": ""})
    with TestClient(create_app(dev, script=StubScript())) as client:
        assert client.get("/healthz").json()["auth_mode"] == "dev"
        assert client.get("/api/me").status_code == 401  # still default-deny
        r = client.post("/api/auth/dev-login", json={"email": "e2e@example.com", "name": "E2E"})
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
        assert me["email"] == "e2e@example.com" and me["auth_mode"] == "dev" and me["baseline_version"]
        again = client.post("/api/auth/dev-login", json={"email": "E2E@example.com"}).json()
        assert again["user_id"] == me["id"]  # stable id per email
        assert client.post("/api/auth/dev-login", json={"email": "nope"}).status_code == 422
        assert client.get("/openapi.json").status_code == 200  # the schema is served in dev only


def test_no_auth_configured_fails_at_startup(test_settings: Settings):
    bare = test_settings.model_copy(update={"supabase_jwt_secret": "", "supabase_url": ""})
    with pytest.raises(RuntimeError, match="AUTH_MODE=supabase needs"), TestClient(create_app(bare)):
        pass


# ---------------------------------------------------------------- SSE stream tokens
def test_stream_token_is_single_use_and_bound_to_the_case(test_settings: Settings):
    app = create_app(test_settings, script=StubScript({"decide": ["needs_review"]}))
    with auth_client(app) as client:
        anon = TestClient(app)  # what a browser EventSource is: no Authorization header
        ids = []
        for name in ("a.pdf", "b.pdf"):
            case = client.post("/api/cases", files=[("files", (name, b"%PDF-1.4 x", "application/pdf"))]).json()
            wait_status(client, case["id"], {"needs_review"})
            ids.append(case["id"])
        a, b = ids

        assert anon.get(f"/api/cases/{a}/events/stream?follow=false").status_code == 401
        minted = client.post(f"/api/cases/{a}/stream-token")
        assert minted.status_code == 200 and minted.json()["expires_in"] == 60
        url = minted.json()["url"]
        assert url.startswith(f"/api/cases/{a}/events/stream?t=")
        r = anon.get(url + "&follow=false")
        assert r.status_code == 200
        assert any(e == "stage_event" for e, _ in parse_sse(r.text))
        assert anon.get(url + "&follow=false").status_code == 401  # single use

        token = client.post(f"/api/cases/{a}/stream-token").json()["url"].split("t=", 1)[1]
        assert anon.get(f"/api/cases/{b}/events/stream?t={token}&follow=false").status_code == 401  # other case
        assert anon.get(f"/api/cases/{a}/events/stream?t={token}&follow=false").status_code == 200

        expired = client.post(f"/api/cases/{a}/stream-token").json()["url"]
        with app.state.session_factory() as s:
            s.execute(update(StreamToken).values(expires_at=utcnow() - timedelta(seconds=1)))
            s.commit()
        assert anon.get(expired + "&follow=false").status_code == 401
        # a stream token opens the stream only, never another route
        assert anon.get(f"/api/cases/{a}?t={token}").status_code == 401

        # another user cannot mint a token for this case
        bob = TestClient(app, headers=auth_headers(BOB))
        assert bob.post(f"/api/cases/{a}/stream-token").status_code == 404


# ---------------------------------------------------------------- file tokens
def test_file_tokens(test_settings: Settings):
    app = create_app(test_settings, script=StubScript({"decide": ["needs_review"]}))
    with auth_client(app) as client:
        anon = TestClient(app)
        case = client.post("/api/cases", files=[("files", ("a.pdf", b"%PDF-1.4 x", "application/pdf"))]).json()
        detail = wait_status(client, case["id"], {"needs_review"})
        doc = detail["documents"][0]
        path = f"/api/cases/{case['id']}/documents/{doc['id']}/file"
        assert anon.get(path).status_code == 401

        signed = client.post("/api/me/file-token", json={"path": path})
        assert signed.status_code == 200, signed.text
        url = signed.json()["url"]
        r = anon.get(url)
        assert r.status_code == 200 and r.content.startswith(b"%PDF")
        assert anon.get(url).status_code == 200  # stateless: reusable until it expires
        ft = url.split("ft=", 1)[1]
        assert anon.get(f"/api/cases/{case['id']}?ft={ft}").status_code == 401  # not a file route
        assert anon.get(f"/api/cases/{case['id']}/audit-pack?ft={ft}").status_code == 401  # another file
        assert anon.get(f"{path}?ft={ft[:-2]}xx").status_code == 401  # tampered

        assert client.post("/api/me/file-token", json={"path": "/api/cases/1"}).status_code == 422
        assert client.post("/api/me/file-token", json={"path": f"{path}/../../x"}).status_code == 422

        # Bob can sign a link to Alice's path, but it authenticates as Bob: the route answers 404
        bob = TestClient(app, headers=auth_headers(BOB))
        bob_url = bob.post("/api/me/file-token", json={"path": path}).json()["url"]
        assert anon.get(bob_url).status_code == 404


def test_file_token_expiry_and_binding():
    ft = FileTokens("secret")
    tok = ft.sign("user-1", "/api/exports/1/download", 60, now=1000)
    assert ft.verify(tok, "/api/exports/1/download", now=1059) == "user-1"
    assert ft.verify(tok, "/api/exports/1/download", now=1061) is None
    assert ft.verify(tok, "/api/exports/2/download", now=1000) is None
    assert ft.verify(tok.replace("user-1", "user-2", 1), "/api/exports/1/download", now=1000) is None
    assert FileTokens("other").verify(tok, "/api/exports/1/download", now=1000) is None
    assert ft.verify("garbage", "/x") is None


# ---------------------------------------------------------------- tokens never reach the logs
def test_redact():
    line = '127.0.0.1:5 - "GET /api/cases/3/events/stream?t=SECRET1&follow=false HTTP/1.1" 200'
    assert "SECRET1" not in redact(line) and "/api/cases/3/events/stream" in redact(line)
    assert "SECRET2" not in redact("GET /portal/SECRET2/upload")
    assert redact("GET /portal/SECRET2/upload") == "GET /portal/[redacted]/upload"
    assert "SECRET3" not in redact("Authorization: Bearer SECRET3")
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/api/x/download?ft=SECRET4", "1.1", 200), None)
    RedactTokens().filter(rec)
    assert "SECRET4" not in rec.getMessage() and "/api/x/download" in rec.getMessage()


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def live_server(test_settings: Settings) -> Iterator[tuple[str, _Capture]]:
    app = create_app(test_settings, script=StubScript({"decide": ["needs_review"]}))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="info", access_log=True))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not srv.started:
        assert time.monotonic() < deadline and thread.is_alive(), "uvicorn did not start"
        time.sleep(0.02)
    cap = _Capture()
    targets = [logging.getLogger("uvicorn.access"), logging.getLogger()]
    for lg in targets:
        lg.addHandler(cap)
    try:
        yield f"http://127.0.0.1:{srv.servers[0].sockets[0].getsockname()[1]}", cap
    finally:
        for lg in targets:
            lg.removeHandler(cap)
        srv.should_exit = True
        thread.join(20)


def test_url_tokens_are_redacted_from_access_logs(live_server):
    base, cap = live_server
    with httpx.Client(base_url=base, headers=auth_headers(), timeout=30) as client, \
            httpx.Client(base_url=base, timeout=30) as anon:
        case = client.post("/api/cases", files={"files": ("a.pdf", b"%PDF-1.4 x", "application/pdf")}).json()
        for _ in range(300):
            detail = client.get(f"/api/cases/{case['id']}").json()
            if detail["status"] == "needs_review":
                break
            time.sleep(0.05)
        doc = detail["documents"][0]
        stream_url = client.post(f"/api/cases/{case['id']}/stream-token").json()["url"]
        file_url = client.post("/api/me/file-token",
                               json={"path": f"/api/cases/{case['id']}/documents/{doc['id']}/file"}).json()["url"]
        assert anon.get(stream_url + "&follow=false").status_code == 200
        assert anon.get(file_url).status_code == 200
        assert anon.get("/portal/a-portal-token-value").status_code == 401
    time.sleep(0.2)  # the access log line is written after the response
    secrets_ = [stream_url.split("t=", 1)[1], file_url.split("ft=", 1)[1], "a-portal-token-value"]
    logged = "\n".join(cap.lines)
    assert "events/stream" in logged and "/file" in logged  # the requests were logged...
    for s in secrets_:
        assert s not in logged  # ...without their tokens
    assert make_token(ALICE)[:40] not in logged
