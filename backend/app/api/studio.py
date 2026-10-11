"""Agent Studio API: config files, versions, validate-before-save, reset, agent stats, test-agent dry run."""

from __future__ import annotations

import threading
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api.deps import get_orchestrator, get_session, get_settings_dep, get_storage
from app.api.schemas import (
    AgentStatOut,
    ConfigFileDetailOut,
    ConfigFileOut,
    ConfigSavedOut,
    ConfigSaveIn,
    ConfigValidateIn,
    ConfigValidateOut,
    TestAgentIn,
    TestAgentOut,
)
from app.config_loader import ConfigError, reset_override, resolved_config, save_override
from app.orchestrator import Orchestrator
from app.settings import Settings
from app.storage import Storage
from app.studio import (
    StudioError,
    agent_stats,
    config_with,
    dry_run,
    file_view,
    list_files,
    require_path,
    validate_edit,
)

router = APIRouter(prefix="/api/config", tags=["config"])

# saves and resets are validated against the overrides in the DB, so they run one at a time: two concurrent
# edits that are each valid alone cannot combine into an invalid config
_SAVE_LOCK = threading.Lock()

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def _studio_error(e: StudioError) -> HTTPException:
    return HTTPException(e.status, str(e))


def _invalid(e: ConfigError) -> HTTPException:
    return HTTPException(422, {"message": "Invalid config: nothing was saved.", "errors": e.errors})


@router.get("/files", response_model=list[ConfigFileOut])
def files(s: SessionDep, settings: SettingsDep) -> list[dict[str, Any]]:
    return list_files(s, settings.config_dir)


@router.get("/agent-stats", response_model=dict[str, AgentStatOut])
def stats(s: SessionDep) -> dict[str, dict[str, Any]]:
    """Runs, failures, average latency and cost per agent (Agent Studio list)."""
    return agent_stats(s)


@router.post("/validate", response_model=ConfigValidateOut)
def validate(body: ConfigValidateIn, s: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """Check edits against the whole config without saving (inline errors while typing)."""
    for path in body.edits:
        try:
            require_path(s, settings.config_dir, path)
        except StudioError as e:
            raise _studio_error(e) from e
    errors = validate_edit(s, settings.config_dir, body.edits)
    return {"ok": not errors, "errors": errors}


@router.post("/test-agent", response_model=TestAgentOut)
async def test_agent(
    body: TestAgentIn,
    request: Request,
    s: SessionDep,
    settings: SettingsDep,
    orch: Annotated[Orchestrator, Depends(get_orchestrator)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> dict[str, Any]:
    """Run one agent action on a case with the current config (plus unsaved `edits`). Dry run: the agent
    works on a throwaway copy of the data, so nothing it writes is kept."""
    try:
        cfg = config_with(s, settings.config_dir, body.edits)
    except ConfigError as e:
        raise _invalid(e) from e
    engine = s.get_bind()
    if not isinstance(engine, Engine):
        raise HTTPException(500, "the session is not bound to an engine")
    try:
        # on the orchestrator loop: the LLM client is only ever used from there
        return await orch.call(
            dry_run(engine, cfg, request.app.state.llm, storage, body.agent, body.action, body.case_id)
        )
    except StudioError as e:
        raise _studio_error(e) from e


@router.get("/{path:path}", response_model=ConfigFileDetailOut)
def get_file(path: str, s: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    try:
        return file_view(s, settings.config_dir, path)
    except StudioError as e:
        raise _studio_error(e) from e


@router.put("/{path:path}", response_model=ConfigSavedOut)
def put_file(path: str, body: ConfigSaveIn, request: Request, s: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """Validate the whole config with this edit; only a valid config is stored (as a new active version).
    New cases run on it; cases already started keep the snapshot they began with."""
    with _SAVE_LOCK:
        try:
            require_path(s, settings.config_dir, path)
            config_with(s, settings.config_dir, {path: body.content})  # incl. handler checks
            cfg = save_override(s, settings.config_dir, path, body.content, body.author)
        except StudioError as e:
            raise _studio_error(e) from e
        except ConfigError as e:
            s.rollback()
            raise _invalid(e) from e
    request.app.state.config_hash = cfg.hash
    return {**file_view(s, settings.config_dir, path), "config_hash": cfg.hash}


@router.delete("/{path:path}", response_model=ConfigSavedOut)
def reset_file(path: str, request: Request, s: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """Reset to file: deactivate the overrides for this path (their history is kept). Refused when the file
    would not validate together with the other active overrides."""
    try:
        files, _ = require_path(s, settings.config_dir, path)
    except StudioError as e:
        raise _studio_error(e) from e
    if path not in files:
        raise HTTPException(409, f"'{path}' exists only as an Agent Studio version; there is no file to reset to")
    with _SAVE_LOCK:
        errors = validate_edit(s, settings.config_dir, {path: files[path]})
        if errors:
            raise _invalid(ConfigError(errors))
        reset_override(s, path)
        cfg = resolved_config(settings.config_dir, s)
    request.app.state.config_hash = cfg.hash
    return {**file_view(s, settings.config_dir, path), "config_hash": cfg.hash}
