"""Authentication: a global default-deny gate in front of every route, Supabase JWT verification, the user
upsert (plus workspace seeding) on first sight, and the AUTH_MODE=dev stub login.

The gate is an app-level dependency (app.main), so a new route is private unless its path is added to
`PUBLIC_PATHS`. A request authenticates with `Authorization: Bearer <Supabase access token>`; the only other
credentials are the URL tokens of app.tokens, and each is accepted on its own routes only:
- `?t=` single-use stream token on the SSE route (EventSource cannot send a header)
- `?ft=` HMAC file token on the file routes (<img>, react-pdf and download links cannot either)
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request
from jwt.types import Options
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import tokens
from app.models import User, utcnow
from app.settings import Settings

log = logging.getLogger(__name__)

AUDIENCE = "authenticated"
DEV_ISSUER = "ledgerflow-dev"
PUBLIC_PATHS = frozenset({"/healthz"})
DEV_LOGIN_PATH = "/api/auth/dev-login"
STREAM_ROUTES = frozenset({"/api/cases/{case_id}/events/stream"})
FILE_ROUTES = frozenset({
    "/api/cases/{case_id}/documents/{doc_id}/file",
    "/api/cases/{case_id}/documents/{doc_id}/pages/{page}.png",
    "/api/cases/{case_id}/template/{version}/download",
    "/api/cases/{case_id}/audit-pack",
    "/api/exports/{batch_id}/download",
    "/api/fixtures/{scenario_id}/files/{filename}",
    "/api/master/{entity}.csv",
})
JWKS_TTL_S = 3600
JWKS_MIN_REFETCH_S = 30  # an unknown `kid` refetches the key set at most this often


class AuthError(Exception):
    """401: missing, invalid or expired credentials."""


@dataclass(frozen=True)
class Identity:
    id: str
    email: str
    name: str | None = None
    avatar_url: str | None = None


# ---------------------------------------------------------------- JWT verification
class JWTVerifier:
    """Verifies Supabase access tokens: asymmetric keys (RS256/ES256) from the project JWKS, or HS256 with the
    legacy JWT secret, and HS256 dev tokens when AUTH_MODE=dev. `aud` must be "authenticated"; `exp` and
    `sub` are required."""

    def __init__(
        self,
        *,
        jwks_url: str = "",
        hs256_secret: str = "",
        issuer: str | None = None,
        dev_secret: str | None = None,
        fetch_jwks: Callable[[], dict[str, Any]] | None = None,
    ):
        self.issuer = issuer
        self.hs256_secret = hs256_secret
        self.dev_secret = dev_secret
        self._jwks_url = jwks_url
        self._fetch = fetch_jwks or (self._http_fetch if jwks_url else None)
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> JWTVerifier:
        dev = (settings.dev_jwt_secret or secrets.token_urlsafe(32)) if settings.auth_mode == "dev" else None
        return cls(jwks_url=settings.jwks_url, hs256_secret=settings.supabase_jwt_secret,
                   issuer=settings.jwt_issuer, dev_secret=dev)

    @property
    def configured(self) -> bool:
        return bool(self._fetch or self.hs256_secret or self.dev_secret)

    def _http_fetch(self) -> dict[str, Any]:
        r = httpx.get(self._jwks_url, timeout=10)
        r.raise_for_status()
        return r.json()

    def _key(self, kid: str | None) -> jwt.PyJWK:
        with self._lock:
            now = time.monotonic()
            stale = now - self._fetched_at > JWKS_TTL_S
            unknown = kid not in self._keys and now - self._fetched_at > JWKS_MIN_REFETCH_S
            if self._fetch is not None and (stale or unknown):
                try:
                    data = self._fetch()
                except (httpx.HTTPError, ValueError) as e:
                    log.warning("JWKS fetch failed: %s", type(e).__name__)
                else:
                    self._keys = {k["kid"]: jwt.PyJWK(k) for k in data.get("keys", []) if k.get("kid")}
                    self._fetched_at = now
            key = self._keys.get(kid or "")
        if key is None:
            raise AuthError("unknown signing key")
        return key

    def verify(self, token: str) -> Identity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise AuthError("malformed token") from e
        alg = header.get("alg")
        options: Options = {"require": ["exp", "sub", "aud"]}
        try:
            if alg in ("RS256", "ES256"):
                key = self._key(header.get("kid"))
                claims = jwt.decode(token, key, algorithms=[alg], audience=AUDIENCE, issuer=self.issuer,
                                    options=options, leeway=30)
            elif alg == "HS256" and self.dev_secret and _issuer(token) == DEV_ISSUER:
                claims = jwt.decode(token, self.dev_secret, algorithms=["HS256"], audience=AUDIENCE,
                                    issuer=DEV_ISSUER, options=options)
            elif alg == "HS256" and self.hs256_secret:
                claims = jwt.decode(token, self.hs256_secret, algorithms=["HS256"], audience=AUDIENCE,
                                    issuer=self.issuer, options=options, leeway=30)
            else:
                raise AuthError(f"unsupported token algorithm {alg!r}")
        except jwt.ExpiredSignatureError as e:
            raise AuthError("token expired") from e
        except jwt.PyJWTError as e:
            raise AuthError("invalid token") from e
        if claims.get("role", AUDIENCE) != AUDIENCE:
            raise AuthError("not a signed-in user")
        meta = claims.get("user_metadata") or {}
        return Identity(
            id=str(claims["sub"]),
            email=str(claims.get("email") or ""),
            name=meta.get("full_name") or meta.get("name"),
            avatar_url=meta.get("avatar_url") or meta.get("picture"),
        )

    def issue_dev_token(self, email: str, name: str | None, ttl_s: int = 3600) -> tuple[str, str]:
        """AUTH_MODE=dev only: (access token, user id) for any email. The id is stable per email."""
        if not self.dev_secret:
            raise AuthError("dev login is disabled")
        uid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ledgerflow-dev:{email.strip().lower()}"))
        now = int(time.time())
        claims = {"sub": uid, "email": email, "aud": AUDIENCE, "role": AUDIENCE, "iss": DEV_ISSUER,
                  "iat": now, "exp": now + ttl_s, "user_metadata": {"full_name": name or email.split("@")[0]}}
        return jwt.encode(claims, self.dev_secret, algorithm="HS256"), uid


def _issuer(token: str) -> str | None:
    try:
        return jwt.decode(token, options={"verify_signature": False}).get("iss")
    except jwt.PyJWTError:
        return None


def check_dev_mode(settings: Settings) -> None:
    """AUTH_MODE=dev hands out a session for any email: refuse it anywhere near production."""
    if settings.auth_mode not in ("supabase", "dev"):
        raise RuntimeError(f"AUTH_MODE must be 'supabase' or 'dev', not {settings.auth_mode!r}")
    if settings.auth_mode != "dev":
        return
    reasons = []
    if settings.storage_backend == "supabase":
        reasons.append("STORAGE_BACKEND=supabase")
    if not settings.database_url.startswith("sqlite"):
        reasons.append("the database is not SQLite")
    if settings.render or os.environ.get("RENDER"):
        reasons.append("RENDER is set")
    if reasons:
        raise RuntimeError(f"AUTH_MODE=dev refuses to start: {', '.join(reasons)}. It is for local and e2e runs only.")


# ---------------------------------------------------------------- users
def upsert_user(sf: sessionmaker[Session], ident: Identity, touch_after_s: int) -> User:
    """The user row for `ident`, created on first sight (the gate then seeds its workspace)."""
    with sf() as s:
        user = s.get(User, ident.id)
        if user is None:
            s.add(User(id=ident.id, email=ident.email, name=ident.name, avatar_url=ident.avatar_url))
            try:
                s.commit()
            except IntegrityError:  # a concurrent first request created it
                s.rollback()
            user = s.get(User, ident.id)
            assert user is not None
        elif user.deleted_at is not None:
            raise AuthError("this account was deleted")
        now = utcnow()
        changed = (ident.email and user.email != ident.email) or (ident.name and user.name != ident.name)
        if changed or now - user.last_seen_at > timedelta(seconds=touch_after_s):
            user.email = ident.email or user.email
            user.name = ident.name or user.name
            user.avatar_url = ident.avatar_url or user.avatar_url
            user.last_seen_at = now
            s.commit()
    return user


# ---------------------------------------------------------------- the gate
def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def auth_gate(request: Request) -> None:
    """Global dependency: everything except `PUBLIC_PATHS` needs a signed-in user (request.state.user)."""
    state = request.app.state
    route = request.scope.get("route")
    path: str = getattr(route, "path", request.url.path)
    if path in state.public_paths:
        return
    sf: sessionmaker[Session] = state.session_factory
    auth_cfg = state.base_config.app.auth
    try:
        token = _bearer(request)
        if token:
            user = upsert_user(sf, state.verifier.verify(token), auth_cfg.last_seen_update_s)
            if user.baseline_version is None:  # first sign-in: copy the demo data into the workspace
                user = state.baseline.ensure_workspace(sf, user.id)
        elif path in STREAM_ROUTES and request.query_params.get("t"):
            user = _stream_user(sf, request.query_params["t"], request.path_params.get("case_id"))
        elif path in FILE_ROUTES and request.query_params.get("ft"):
            owner = state.file_tokens.verify(request.query_params["ft"], request.url.path)
            user = _load_user(sf, owner)
        else:
            raise AuthError("sign in required")
    except AuthError as e:
        raise _unauthorized(str(e)) from None
    request.state.user = user


def _load_user(sf: sessionmaker[Session], user_id: str | None) -> User:
    if not user_id:
        raise AuthError("invalid or expired link")
    with sf() as s:
        user = s.get(User, user_id)
    if user is None or user.deleted_at is not None or user.baseline_version is None:
        raise AuthError("invalid or expired link")
    return user


def _stream_user(sf: sessionmaker[Session], token: str, case_id: Any) -> User:
    try:
        cid = int(case_id)
    except (TypeError, ValueError):
        raise AuthError("invalid stream token") from None
    with sf() as s:
        owner = tokens.redeem_stream_token(s, token, cid)
        s.commit()
    if owner is None:
        raise AuthError("invalid, used or expired stream token")
    return _load_user(sf, owner)


def current_user(request: Request) -> User:
    """The signed-in user the gate put on the request."""
    user = getattr(request.state, "user", None)
    if user is None:  # a public route asked for a user: a wiring bug, never a valid request
        raise _unauthorized("sign in required")
    return user
