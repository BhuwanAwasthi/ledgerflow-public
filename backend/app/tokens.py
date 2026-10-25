"""Short-lived URL tokens for requests that cannot carry an Authorization header.

- SSE stream tokens: browser EventSource sends no headers. `POST /api/cases/{id}/stream-token` returns a
  random single-use token bound to the user and the case (only its sha256 is stored, in stream_tokens);
  the stream endpoint redeems it atomically.
- File tokens: <img>, react-pdf and download links cannot send a header either. A file token is stateless:
  HMAC-SHA256 over (user, exact path, expiry), so it opens one file for one user for a minute.

Tokens never reach a log: app.log_redaction strips query strings (and portal tokens) from every log line.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import timedelta

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.models import StreamToken, utcnow


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- SSE stream tokens
def issue_stream_token(s: Session, owner_id: str, case_id: int, ttl_s: int) -> str:
    """A fresh single-use token for `case_id` (caller commits). Expired tokens are swept on the way."""
    now = utcnow()
    s.execute(delete(StreamToken).where(StreamToken.expires_at < now - timedelta(hours=1)))
    token = secrets.token_urlsafe(32)
    s.add(StreamToken(token_hash=_hash(token), owner_id=owner_id, case_id=case_id,
                      expires_at=now + timedelta(seconds=ttl_s)))
    return token


def redeem_stream_token(s: Session, token: str, case_id: int) -> str | None:
    """The owner id when `token` is valid for `case_id`, unexpired and unused; marks it used (one UPDATE, so
    two concurrent redemptions cannot both succeed). None otherwise. Caller commits."""
    now = utcnow()
    owner = s.execute(
        update(StreamToken)
        .where(
            StreamToken.token_hash == _hash(token),
            StreamToken.case_id == case_id,
            StreamToken.used_at.is_(None),
            StreamToken.expires_at > now,
        )
        .values(used_at=now)
        .returning(StreamToken.owner_id)
    ).scalar_one_or_none()
    return owner


# ---------------------------------------------------------------- file tokens
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class FileTokens:
    """Stateless `<owner>.<expiry>.<signature>` tokens bound to one URL path."""

    def __init__(self, secret: str | None):
        self._key = secret.encode() if secret else secrets.token_bytes(32)

    def _sig(self, owner_id: str, path: str, exp: int) -> str:
        return _b64(hmac.new(self._key, f"{owner_id}|{path}|{exp}".encode(), hashlib.sha256).digest())

    def sign(self, owner_id: str, path: str, ttl_s: int, *, now: float | None = None) -> str:
        exp = int((now if now is not None else time.time()) + ttl_s)
        return f"{owner_id}.{exp}.{self._sig(owner_id, path, exp)}"

    def verify(self, token: str, path: str, *, now: float | None = None) -> str | None:
        """The owner id when `token` was signed for exactly `path` and has not expired."""
        try:
            owner_id, exp_s, sig = token.split(".")
            exp = int(exp_s)
        except ValueError:
            return None
        if exp < (now if now is not None else time.time()):
            return None
        if not hmac.compare_digest(sig, self._sig(owner_id, path, exp)):
            return None
        return owner_id
