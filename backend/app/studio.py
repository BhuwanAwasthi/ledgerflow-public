"""Agent Studio: config files with their DB overrides and versions, validation of an edit before it is saved,
per-agent run stats, and the "Test agent" dry run.

A dry run executes one agent action on a case against a throwaway copy of the data: an in-memory copy of a
SQLite database, or a transaction that is never committed on Postgres. Storage writes stay in memory. So the
agent behaves exactly as in the pipeline (it may write rows, reserve PO lines, draft templates) and nothing it
does survives the call.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.base import AgentContext, build_registry, load_handler
from app.case_state import fold_state
from app.config_loader import (
    ConfigError,
    ResolvedConfig,
    build_config,
    load_overrides,
    read_config_files,
)
from app.db import dumps
from app.llm import LLM, AzureLLM
from app.models import Case, ConfigVersion, StageEvent, StageStatus
from app.storage import Storage

log = logging.getLogger(__name__)


class StudioError(ValueError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def file_kind(path: str) -> str:
    """Which Studio tab a config path belongs to."""
    if path.startswith("agents/"):
        return "agent"
    if path.startswith("prompts/"):
        return "prompt"
    if path.startswith("erp_profiles/"):
        return "erp_profile"
    return Path(path).stem  # app | workflow | rules | document_types


def _known_paths(session: Session, config_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    return read_config_files(config_dir), load_overrides(session)


def require_path(session: Session, config_dir: Path, path: str) -> tuple[dict[str, str], dict[str, str]]:
    """Studio edits existing config files only (a new agent also needs a Python class in app/agents/)."""
    files, overrides = _known_paths(session, config_dir)
    if path not in files and path not in overrides:
        raise StudioError(404, f"no config file '{path}'")
    return files, overrides


def _versions(session: Session, path: str) -> list[ConfigVersion]:
    return list(
        session.scalars(
            select(ConfigVersion).where(ConfigVersion.path == path).order_by(ConfigVersion.version.desc())
        ).all()
    )


def list_files(session: Session, config_dir: Path) -> list[dict[str, Any]]:
    files, overrides = _known_paths(session, config_dir)
    counts: dict[str, int] = {}
    for p, n in session.execute(select(ConfigVersion.path, func.count()).group_by(ConfigVersion.path)).all():
        counts[p] = int(n)
    out = []
    for path in sorted(set(files) | set(overrides)):
        out.append(
            {
                "path": path,
                "kind": file_kind(path),
                "overridden": path in overrides,
                "versions": counts.get(path, 0),
            }
        )
    return out


def file_view(session: Session, config_dir: Path, path: str) -> dict[str, Any]:
    files, overrides = require_path(session, config_dir, path)
    versions = _versions(session, path)
    active = next((v for v in versions if v.active), None)
    return {
        "path": path,
        "kind": file_kind(path),
        "content": overrides.get(path, files.get(path, "")),
        "file_content": files.get(path),
        "overridden": path in overrides,
        "active_version": active.version if active else None,
        "versions": [
            {
                "version": v.version,
                "active": v.active,
                "author": v.author,
                "created_at": v.created_at,
                "content": v.content,
            }
            for v in versions
        ],
    }


HANDLER_PACKAGE = "app.agents."


def handler_errors(cfg: ResolvedConfig) -> list[str]:
    """Every agent's `handler` must import to an Agent class in app.agents. The registry would otherwise run a
    stub in its place (a typo in a module name) or fail every case (a wrong class name)."""
    errors: list[str] = []
    for agent in cfg.agents.values():
        where = f"agents/{agent.id}.yaml: handler '{agent.handler}'"
        if not agent.handler.startswith(HANDLER_PACKAGE):
            errors.append(f"{where}: must be a class in {HANDLER_PACKAGE.rstrip('.')}")
            continue
        try:
            if load_handler(agent.handler) is None:
                errors.append(f"{where}: module not found")
        except (ImportError, AttributeError, TypeError) as e:
            errors.append(f"{where}: {type(e).__name__}: {e}")
    return errors


def config_with(session: Session, config_dir: Path, edits: dict[str, str] | None) -> ResolvedConfig:
    """The whole config with these edits applied, including handler checks. Raises ConfigError; saves nothing."""
    files, overrides = _known_paths(session, config_dir)
    try:
        cfg = build_config({**files, **overrides, **(edits or {})})
    except ConfigError as e:
        # an edited file that is invalid on its own drops out, and every reference to it fails too
        # ("unknown agent 'policy'"): report the edited file's own errors only
        own = [m for m in e.errors if any(m.startswith(f"{p}:") for p in edits or {})]
        raise ConfigError(own or e.errors) from e
    errors = handler_errors(cfg)
    if errors:
        raise ConfigError(errors)
    return cfg


def validate_edit(session: Session, config_dir: Path, edits: dict[str, str]) -> list[str]:
    """Errors the whole config would have with these edits applied ([] = valid). Nothing is saved."""
    try:
        config_with(session, config_dir, edits)
    except ConfigError as e:
        return e.errors
    return []


def agent_stats(session: Session) -> dict[str, dict[str, Any]]:
    """Per agent: runs, failures, average latency and cost over completed/waiting/failed stage events."""
    rows = session.execute(
        select(StageEvent.agent_id, StageEvent.status, StageEvent.metrics).where(
            StageEvent.agent_id.is_not(None),
            StageEvent.status.in_([StageStatus.completed, StageStatus.waiting, StageStatus.failed]),
        )
    ).all()
    acc: dict[str, dict[str, Any]] = {}
    for agent_id, status, metrics in rows:
        if agent_id is None:
            continue
        a = acc.setdefault(agent_id, {"runs": 0, "failed": 0, "latency": [], "cost": [], "tokens": 0})
        a["runs"] += 1
        if status == StageStatus.failed:
            a["failed"] += 1
        m = metrics or {}
        if isinstance(m.get("latency_ms"), (int, float)):
            a["latency"].append(float(m["latency_ms"]))
        if isinstance(m.get("cost_usd"), (int, float)):
            a["cost"].append(float(m["cost_usd"]))
        a["tokens"] += int(m.get("tokens_in") or 0) + int(m.get("tokens_out") or 0)
    out: dict[str, dict[str, Any]] = {}
    for agent_id, a in acc.items():
        out[agent_id] = {
            "runs": a["runs"],
            "failed": a["failed"],
            "avg_latency_ms": round(sum(a["latency"]) / len(a["latency"])) if a["latency"] else None,
            "avg_cost_usd": sum(a["cost"]) / len(a["cost"]) if a["cost"] else None,
            "tokens": a["tokens"],
        }
    return out


# ---------------- dry run
class MemoryWriteStorage:
    """Reads from the real storage; writes are kept in memory and readable for the rest of the dry run."""

    def __init__(self, inner: Storage):
        self.inner = inner
        self.name = f"dry-run:{inner.name}"
        self.written: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        self.written[key] = data
        return key

    def get(self, key: str) -> bytes:
        return self.written[key] if key in self.written else self.inner.get(key)

    def health(self) -> bool:
        return self.inner.health()


@contextmanager
def scratch_sessions(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Sessions whose commits never reach the real database."""
    if engine.dialect.name == "sqlite":
        mem = create_engine(
            "sqlite://",
            json_serializer=dumps,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        src = engine.raw_connection()
        dst = mem.raw_connection()
        try:
            src_db, dst_db = src.driver_connection, dst.driver_connection
            assert isinstance(src_db, sqlite3.Connection) and isinstance(dst_db, sqlite3.Connection)
            src_db.backup(dst_db)
        finally:
            src.close()
            dst.close()
        try:
            yield sessionmaker(bind=mem, expire_on_commit=False)
        finally:
            mem.dispose()
        return
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # each session works in a savepoint: its commit or rollback never ends the outer transaction,
            # which is rolled back below
            yield sessionmaker(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
        finally:
            if trans.is_active:
                trans.rollback()
            else:
                conn.rollback()


async def dry_run(
    engine: Engine,
    cfg: ResolvedConfig,
    llm: LLM | None,
    storage: Storage,
    agent_id: str,
    action: str,
    case_id: int,
) -> dict[str, Any]:
    agent_cfg = cfg.agents.get(agent_id)
    if agent_cfg is None:
        raise StudioError(404, f"no agent '{agent_id}'")
    if action not in agent_cfg.actions:
        raise StudioError(422, f"agent '{agent_id}' has no action '{action}' (available: {', '.join(agent_cfg.actions)})")
    try:
        agent = build_registry(cfg)[agent_id]
    except (ImportError, AttributeError, TypeError) as e:
        raise StudioError(422, f"agents/{agent_id}.yaml: handler '{agent_cfg.handler}' does not load: {e}") from e
    declared = agent_cfg.actions[action].outcomes
    timeout = agent_cfg.timeout_s or cfg.app.limits.stage_timeout_s
    stage = next((s.id for s in cfg.workflow.stages if s.agent == agent_id and s.action == action), f"{agent_id}.{action}")
    progress: list[str] = []

    async def emit(msg: str) -> None:
        progress.append(msg)

    scratch_storage = MemoryWriteStorage(storage)
    with scratch_sessions(engine) as sf:
        if isinstance(llm, AzureLLM):
            llm = copy.copy(llm)  # its response cache writes go to the scratch copy too
            llm.session_factory = sf
        with sf() as s:
            case = s.get(Case, case_id)
            if case is None:
                raise StudioError(404, f"case {case_id} not found")
            ref = case.ref
            state = fold_state(s, case_id)
        ctx = AgentContext(
            case_id=case_id,
            case_ref=ref,
            stage_id=stage,
            run_no=0,
            config=cfg,
            agent_config=agent_cfg,
            state=state,
            session_factory=sf,
            llm=llm,
            storage=scratch_storage,
            emit=emit,
        )
        t0 = time.perf_counter()
        base: dict[str, Any] = {
            "agent": agent_id,
            "action": action,
            "case_id": case_id,
            "case_ref": ref,
            "stage_id": stage,
            "config_hash": cfg.hash,
            "progress": progress,
        }
        try:
            result = await asyncio.wait_for(agent.run(action, ctx), timeout=timeout)
        except TimeoutError:
            return {**base, "ok": False, "error": f"timed out after {timeout:g}s", "duration_ms": _ms(t0)}
        except Exception as e:  # report the agent's failure, as the stage card would
            log.warning("test-agent %s.%s on case %s raised", agent_id, action, case_id, exc_info=True)
            return {**base, "ok": False, "error": f"{type(e).__name__}: {e}", "duration_ms": _ms(t0)}
    error = None if result.outcome in declared else f"returned outcome '{result.outcome}', not one of {declared}"
    return {
        **base,
        "ok": error is None,
        "error": error,
        "outcome": result.outcome,
        "data": json.loads(dumps(result.data)),  # Decimals stay exact strings
        "reasoning": result.reasoning,
        "confidence": result.confidence,
        "metrics": json.loads(dumps(result.metrics)),
        "storage_writes": sorted(scratch_storage.written),
        "duration_ms": _ms(t0),
    }


def _ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)
