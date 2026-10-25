"""The signed-in user: profile, workspace reset, short-lived file links. Plus the AUTH_MODE=dev stub login
(`dev_router`, mounted only in dev mode)."""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, get_baseline, get_storage
from app.api.schemas import DevLoginIn, DevLoginOut, FileTokenIn, MeOut, ResetWorkspaceOut, SignedUrlOut
from app.auth import FILE_ROUTES, AuthError, JWTVerifier, upsert_user
from app.baseline import BASELINE_VERSION, Baseline, BaselineError
from app.models import User
from app.storage import Storage

router = APIRouter(prefix="/api/me", tags=["me"])
dev_router = APIRouter(prefix="/api/auth", tags=["auth (dev only)"])

# file routes as patterns: a file token is only ever issued for one of these paths
_FILE_PATTERNS = [re.compile("^" + re.sub(r"\\\{[a-z_]+\\\}", "[^/?#]+", re.escape(p)) + "$") for p in FILE_ROUTES]


def me_out(user: User, auth_mode: str) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "avatar_url": user.avatar_url,
        "mode": user.mode,
        "baseline_version": user.baseline_version,
        "baseline_current": BASELINE_VERSION,
        "baseline_outdated": user.baseline_version is not None and user.baseline_version < BASELINE_VERSION,
        "auth_mode": auth_mode,
    }


@router.get("", response_model=MeOut)
def get_me(request: Request, user: CurrentUser) -> dict[str, Any]:
    return me_out(user, request.app.state.settings.auth_mode)


@router.post("/reset-workspace", response_model=ResetWorkspaceOut)
async def reset_workspace(
    request: Request,
    user: CurrentUser,
    baseline: Annotated[Baseline, Depends(get_baseline)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> dict[str, Any]:
    """Wipe this workspace (cases, documents, exports, master data, files) and seed the demo data again.
    Agent Studio config edits are kept."""
    try:
        rep = await run_in_threadpool(baseline.reset_workspace, request.app.state.session_factory, user.id, storage)
    except BaselineError as e:
        raise HTTPException(e.status, str(e)) from e
    return {**vars(rep), "baseline_version": BASELINE_VERSION}


@router.post("/file-token", response_model=SignedUrlOut)
def file_token(body: FileTokenIn, request: Request, user: CurrentUser) -> dict[str, Any]:
    """A 60-second link to one file route (PDF, page PNG, template, ZIP, audit pack) for <img>, react-pdf and
    download links, which cannot send the Authorization header. The route still checks ownership."""
    if not any(p.match(body.path) for p in _FILE_PATTERNS):
        raise HTTPException(422, f"{body.path} is not a file route")
    ttl = request.app.state.base_config.app.auth.file_token_ttl_s
    token = request.app.state.file_tokens.sign(user.id, body.path, ttl)
    return {"url": f"{body.path}?ft={token}", "expires_in": ttl}


@dev_router.post("/dev-login", response_model=DevLoginOut)
def dev_login(body: DevLoginIn, request: Request) -> dict[str, Any]:
    """AUTH_MODE=dev only (local and e2e): a session for any email, no password. Never mounted otherwise."""
    verifier: JWTVerifier = request.app.state.verifier
    ttl = 3600
    try:
        token, uid = verifier.issue_dev_token(body.email, body.name, ttl)
        state = request.app.state
        user = upsert_user(state.session_factory, verifier.verify(token), state.base_config.app.auth.last_seen_update_s)
        if user.baseline_version is None:
            state.baseline.ensure_workspace(state.session_factory, user.id)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    return {"access_token": token, "user_id": uid, "expires_in": ttl}
