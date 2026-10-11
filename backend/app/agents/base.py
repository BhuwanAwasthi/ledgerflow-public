"""Agent contract (SPEC section 5), the registry that builds agents from config, and StubAgent.

An agent is a class + config/agents/<id>.yaml. The registry imports the YAML `handler`; while a handler
module does not exist yet (phase B1) the agent runs as a StubAgent that returns canned outcomes.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.config_loader import AgentConfig, ResolvedConfig

log = logging.getLogger(__name__)


@dataclass
class AgentResult:
    outcome: str  # must be in the agent YAML's outcomes for that action
    data: dict[str, Any] = field(default_factory=dict)  # merged into case state under the agent's key
    reasoning: list[str] = field(default_factory=list)  # 1-5 plain-English sentences
    confidence: float | None = None
    metrics: dict[str, Any] = field(
        default_factory=dict
    )  # tokens_in, tokens_out, cost_usd, latency_ms, cache_hit


async def _no_emit(msg: str) -> None:
    return None


@dataclass
class AgentContext:
    case_id: int
    case_ref: str | None
    stage_id: str
    run_no: int
    config: ResolvedConfig
    agent_config: AgentConfig
    state: dict[str, Any]  # case state: completed stage outputs folded per agent id
    session_factory: sessionmaker[Session]  # read access to case, documents, master data
    llm: Any | None = None
    emit: Callable[[str], Awaitable[None]] = _no_emit  # sub-step progress lines (live view)

    @property
    def params(self) -> dict[str, Any]:
        return self.agent_config.params


class Agent(ABC):
    id: str

    def __init__(self, config: AgentConfig):
        self.config = config
        self.id = config.id

    @abstractmethod
    async def run(self, action: str, ctx: AgentContext) -> AgentResult: ...


class StubScript:
    """Canned outcomes for StubAgent, keyed by stage id. Each case consumes the list in order and then
    repeats the last entry. Special entries: "!error" raises, "!sleep:<s>" sleeps (to hit the timeout)."""

    def __init__(self, outcomes: dict[str, list[str]] | None = None):
        self.outcomes = outcomes or {}
        self._cursor: dict[tuple[int, str], int] = {}

    def next(self, case_id: int, stage_id: str) -> str | None:
        seq = self.outcomes.get(stage_id)
        if not seq:
            return None
        i = self._cursor.get((case_id, stage_id), 0)
        self._cursor[(case_id, stage_id)] = i + 1
        return seq[min(i, len(seq) - 1)]


class StubAgent(Agent):
    def __init__(self, config: AgentConfig, script: StubScript | None = None):
        super().__init__(config)
        self.script = script

    async def run(self, action: str, ctx: AgentContext) -> AgentResult:
        scripted = self.script.next(ctx.case_id, ctx.stage_id) if self.script else None
        if scripted == "!error":
            raise RuntimeError(f"stub {self.id}.{action} failed on purpose")
        if scripted and scripted.startswith("!sleep:"):
            await asyncio.sleep(float(scripted.split(":", 1)[1]))
            scripted = None
        outcome = scripted or self.config.actions[action].outcomes[0]
        await ctx.emit(f"stub {self.id}.{action}")
        return AgentResult(
            outcome=outcome,
            data={"stub": True, "action": action, "outcome": outcome},
            reasoning=[f"Stub {self.config.name} returned '{outcome}' (real agent not built yet)."],
            confidence=1.0,
            metrics={"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "cache_hit": False},
        )


def load_handler(handler: str) -> type[Agent] | None:
    """Import `module:Class`; None when the module itself does not exist yet."""
    module_name, cls_name = handler.split(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        if e.name and (module_name == e.name or module_name.startswith(e.name + ".")):
            return None
        raise
    cls = getattr(module, cls_name)
    if not (isinstance(cls, type) and issubclass(cls, Agent)):
        raise TypeError(f"{handler} is not an Agent subclass")
    return cls


def build_registry(
    config: ResolvedConfig, *, script: StubScript | None = None, force_stub: bool = False
) -> dict[str, Agent]:
    registry: dict[str, Agent] = {}
    for agent_id, acfg in config.agents.items():
        cls = None if force_stub else load_handler(acfg.handler)
        if cls is None:
            if not force_stub:
                log.warning("agent %s: handler %s not found, using StubAgent", agent_id, acfg.handler)
            registry[agent_id] = StubAgent(acfg, script)
        else:
            registry[agent_id] = cls(acfg)
    return registry
