"""Deterministic state machine over config/workflow.yaml + in-process job runner + SSE event bus.

The orchestrator is code, not an LLM: it runs a stage's agent action, maps the returned outcome to the
next stage, appends a stage_event for every transition and publishes it to SSE subscribers.

Pauses:
- `hitl: true` stage   -> status needs_review until `submit_review` (outcome = review action)
- stage with `wait_for` -> runs its action, then status on_hold until `fire_event(case_id, event)`,
                           which continues at `resume_to`

Threading: the workers, the job queue, the per-case locks and every agent run live on the orchestrator's own
event loop in a background thread ("orchestrator"). Pipeline work is mostly synchronous SQLAlchemy plus LLM
calls, so a slow query or commit there never stalls the API loop that serves SSE. Public coroutines
(submit_review, fire_event, rerun, resume_pending, join, call) hop onto that loop; enqueue and the event bus
are thread-safe.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app import po_ledger
from app.agents.base import Agent, AgentContext, AgentResult, StubScript, build_registry
from app.case_state import fold_state
from app.config_loader import END_PREFIX, SKIPPED, ResolvedConfig, Stage, from_snapshot, snapshot_json
from app.llm import LLM
from app.models import (
    Case,
    CaseStatus,
    ReviewAction,
    ReviewActionRow,
    StageEvent,
    StageStatus,
    utcnow,
)
from app.storage import Storage

log = logging.getLogger(__name__)
T = TypeVar("T")


# terminal statuses that give a PO reservation back (approval consumes it in template.finalize)
RELEASE_ON = frozenset({CaseStatus.rejected, CaseStatus.failed})


class OrchestratorError(Exception):
    """A request that does not fit the case's current state (HTTP 409)."""


# ---------------------------------------------------------------- event bus
class EventBus:
    """Per-case pub/sub. Thread-safe: the orchestrator publishes from its own thread, and each subscriber's
    queue is fed on the loop that subscribed (the API loop for SSE)."""

    def __init__(self) -> None:
        self._subs: dict[int, dict[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]] = defaultdict(dict)
        self._lock = threading.Lock()

    def subscribe(self, case_id: int) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with self._lock:
            self._subs[case_id][q] = asyncio.get_running_loop()
        return q

    def unsubscribe(self, case_id: int, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            subs = self._subs.get(case_id)
            if subs is None:
                return
            subs.pop(q, None)
            if not subs:
                self._subs.pop(case_id, None)

    def publish(self, case_id: int, event: str, data: dict[str, Any]) -> None:
        msg = {"event": event, "data": data}
        with self._lock:
            targets = list(self._subs.get(case_id, {}).items())
        here = _running_loop()
        for q, loop in targets:
            if loop is here:
                q.put_nowait(msg)
                continue
            try:
                loop.call_soon_threadsafe(q.put_nowait, msg)
            except RuntimeError:  # the subscriber's loop is closed (client gone during shutdown)
                pass


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def event_to_dict(ev: StageEvent) -> dict[str, Any]:
    return {
        "id": ev.id,
        "case_id": ev.case_id,
        "run_no": ev.run_no,
        "seq": ev.seq,
        "stage_id": ev.stage_id,
        "agent_id": ev.agent_id,
        "action": ev.action,
        "status": ev.status,
        "outcome": ev.outcome,
        "input_summary": ev.input_summary or {},
        "output": ev.output or {},
        "reasoning": ev.reasoning or [],
        "metrics": ev.metrics or {},
        "started_at": ev.started_at.isoformat() if ev.started_at else None,
        "ended_at": ev.ended_at.isoformat() if ev.ended_at else None,
    }


def case_summary(case: Case) -> dict[str, Any]:
    return {
        "id": case.id,
        "ref": case.ref,
        "status": case.status,
        "current_stage": case.current_stage,
        "decision": case.decision,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


RERUNNABLE = frozenset({CaseStatus.failed, CaseStatus.needs_review, CaseStatus.on_hold})


# ---------------------------------------------------------------- jobs
@dataclass
class Resume:
    """Completes a paused stage before continuing."""

    stage_id: str
    outcome: str | None  # review action for hitl; None for wait_for (re-uses the action's outcome)
    event: str | None = None
    text: str | None = None
    actor: str | None = None


@dataclass
class Job:
    case_id: int
    target: str | None = None  # stage id or end:<status>; None = case.current_stage or workflow start
    run_no: int | None = None  # set when a review/event already opened the run


class Orchestrator:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        config_provider: Callable[[], ResolvedConfig],
        *,
        script: StubScript | None = None,
        force_stub: bool = False,
        llm: LLM | None = None,
        storage: Storage | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.config_provider = config_provider
        self.script = script
        self.force_stub = force_stub
        self.llm = llm
        self.storage = storage
        self.bus = EventBus()
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._configs: dict[str, ResolvedConfig] = {}
        self._registries: dict[str, dict[str, Agent]] = {}

    # ---------------- lifecycle
    async def start(self) -> None:
        """Start the orchestrator thread (its own event loop) and the workers on it."""
        if self._thread is not None:
            return
        ready = threading.Event()

        def serve() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            try:
                loop.run_forever()
            finally:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

        self._thread = threading.Thread(target=serve, name="orchestrator", daemon=True)
        self._thread.start()
        await asyncio.to_thread(ready.wait)
        await self.call(self._start_workers())

    async def _start_workers(self) -> None:
        # asyncio primitives bind to the loop that first waits on them: fresh ones for this loop, keeping
        # jobs enqueued before start
        old, self._queue = self._queue, asyncio.Queue()
        while not old.empty():
            self._queue.put_nowait(old.get_nowait())
        self._locks = defaultdict(asyncio.Lock)
        n = self.config_provider().app.limits.pipeline_concurrency
        self._workers = [asyncio.create_task(self._worker(i)) for i in range(n)]

    async def stop(self) -> None:
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        await self.call(self._stop_workers())
        loop.call_soon_threadsafe(loop.stop)
        await asyncio.to_thread(thread.join)
        self._loop = self._thread = None

    async def _stop_workers(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    def submit(self, coro: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        """Schedule a coroutine on the orchestrator loop from any thread (e.g. the LLM startup probe)."""
        if self._loop is None:
            coro.close()
            raise RuntimeError("orchestrator is not started")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def call(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run a coroutine on the orchestrator loop and await it from any loop. Pipeline work started by an
        API request (portal classification, vendor approval) goes through here, so its database and LLM
        calls never run on the API loop."""
        if self._loop is None or self._loop is _running_loop():
            return await coro
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._loop))

    async def join(self) -> None:
        """Wait until every queued job (and the jobs they enqueue) has finished."""
        await self.call(self._queue.join())

    def enqueue(self, case_id: int, target: str | None = None, run_no: int | None = None) -> None:
        """Thread-safe: called from API handlers (any thread) and from the workers themselves."""
        job = Job(case_id, target, run_no)
        loop = self._loop
        if loop is None or loop is _running_loop():
            self._queue.put_nowait(job)
        else:
            loop.call_soon_threadsafe(self._queue.put_nowait, job)

    async def resume_pending(self) -> int:
        return await self.call(self._resume_pending())

    async def _resume_pending(self) -> int:
        """Startup: restart runs that were processing (from the stage they were in, or the target an
        accepted review/event already moved them to) or never started."""
        with self.session_factory() as s:
            rows = s.scalars(
                select(Case).where(Case.status.in_([CaseStatus.processing, CaseStatus.received]))
            ).all()
            for c in rows:
                self.enqueue(c.id, c.current_stage)
            return len(rows)

    # ---------------- external triggers
    async def submit_review(self, case_id: int, action: str, text: str | None, actor: str) -> None:
        await self.call(self._submit_review(case_id, action, text, actor))

    async def _submit_review(self, case_id: int, action: str, text: str | None, actor: str) -> None:
        if action not in {a.value for a in ReviewAction}:
            raise OrchestratorError(f"unknown review action '{action}'")
        with self.session_factory() as s:  # fail fast instead of waiting for a running LLM stage
            status = self._get_case(s, case_id).status
        if status != CaseStatus.needs_review:
            raise OrchestratorError(f"case {case_id} is not waiting for review (status {status})")
        async with self._locks[case_id]:
            with self.session_factory() as s:
                case = self._get_case(s, case_id)
                cfg = self.case_config(s, case)
                stage = self._stage_or_none(cfg, case.current_stage)
                if case.status != CaseStatus.needs_review or stage is None or not stage.hitl:
                    raise OrchestratorError(
                        f"case {case.ref} is not waiting for review (status {case.status}, stage {case.current_stage})"
                    )
                s.add(ReviewActionRow(case_id=case_id, actor=actor, action=action, text=text))
                self._accept_resume(s, cfg, case, Resume(stage.id, action, text=text, actor=actor))

    async def fire_event(self, case_id: int, event: str) -> bool:
        """Resume a case waiting on `event`. Returns False if the case is not waiting for it."""
        return await self.call(self._fire_event(case_id, event))

    async def _fire_event(self, case_id: int, event: str) -> bool:
        async with self._locks[case_id]:
            with self.session_factory() as s:
                case = s.get(Case, case_id)
                if case is None:
                    return False
                cfg = self.case_config(s, case)
                stage = self._stage_or_none(cfg, case.current_stage)
                if case.status != CaseStatus.on_hold or stage is None or stage.wait_for != event:
                    return False
                self._accept_resume(s, cfg, case, Resume(stage.id, None, event=event))
                return True

    async def rerun(self, case_id: int, from_stage: str | None = None) -> str:
        """Admin rerun / the live view's Retry: start a new run at `from_stage` (default: the stage the case
        failed or paused at). Only failed or paused cases: approved, exported and rejected cases are final, and
        a running case is refused. Returns the stage the run starts at."""
        return await self.call(self._rerun(case_id, from_stage))

    async def _rerun(self, case_id: int, from_stage: str | None) -> str:
        async with self._locks[case_id]:
            with self.session_factory() as s:
                case = self._get_case(s, case_id)
                if case.status not in RERUNNABLE:
                    raise OrchestratorError(
                        f"case {case.ref} cannot be rerun in status {case.status} "
                        f"(allowed: {', '.join(sorted(RERUNNABLE))})"
                    )
                cfg = self.case_config(s, case)
                target = from_stage or case.current_stage
                if not target or self._stage_or_none(cfg, target) is None:
                    raise OrchestratorError(f"'{target}' is not a stage of this case's workflow")
                case.status = CaseStatus.processing
                case.current_stage = target
                case.sla_due_at = None
                s.commit()
                self.bus.publish(case.id, "case_updated", case_summary(case))
                self.enqueue(case.id, target, self._next_run_no(s, case.id))
                return target

    def _accept_resume(self, s: Session, cfg: ResolvedConfig, case: Case, r: Resume) -> None:
        """Persist the paused stage's completion and the next target before queueing, so an accepted
        review or event survives a restart (resume_pending continues from current_stage)."""
        run_no = self._next_run_no(s, case.id)
        target = self._complete_paused(s, cfg, case, run_no, r)
        case.status = CaseStatus.processing
        case.current_stage = target
        s.commit()
        self.bus.publish(case.id, "case_updated", case_summary(case))
        self.enqueue(case.id, target, run_no)

    @staticmethod
    def _next_run_no(s: Session, case_id: int) -> int:
        return (s.scalar(select(func.max(StageEvent.run_no)).where(StageEvent.case_id == case_id)) or 0) + 1

    # ---------------- config per case
    def case_config(self, s: Session, case: Case) -> ResolvedConfig:
        """The config snapshot the case runs on (taken when it first runs), so decisions are reproducible."""
        if case.config_snapshot is None:
            cfg = self.config_provider()
            case.config_hash = cfg.hash
            case.config_snapshot = snapshot_json(cfg)
            s.commit()
            self._configs.setdefault(cfg.hash, cfg)
            return cfg
        h = case.config_hash or case.config_snapshot["hash"]
        if h not in self._configs:
            self._configs[h] = from_snapshot(case.config_snapshot)
        return self._configs[h]

    def _registry(self, cfg: ResolvedConfig) -> dict[str, Agent]:
        if cfg.hash not in self._registries:
            self._registries[cfg.hash] = build_registry(cfg, script=self.script, force_stub=self.force_stub)
        return self._registries[cfg.hash]

    # ---------------- worker
    async def _worker(self, n: int) -> None:
        while True:
            job = await self._queue.get()
            try:
                async with self._locks[job.case_id]:
                    await self._run(job)
            except Exception as e:
                log.exception("worker %s: job for case %s crashed", n, job.case_id)
                self._mark_failed(job.case_id, f"{type(e).__name__}: {e}")
            finally:
                self._queue.task_done()

    async def _run(self, job: Job) -> None:
        with self.session_factory() as s:
            case = self._get_case(s, job.case_id)
            cfg = self.case_config(s, case)
            wf = cfg.workflow
            registry = self._registry(cfg)
            run_no = job.run_no or self._next_run_no(s, case.id)
            max_steps = cfg.app.limits.max_stages_per_run

            case.status = CaseStatus.processing
            s.commit()
            self.bus.publish(case.id, "case_updated", case_summary(case))

            target = job.target or case.current_stage or wf.start

            for _ in range(max_steps):
                if target.startswith(END_PREFIX):
                    self._set_status(s, cfg, case, CaseStatus(target[len(END_PREFIX) :]), target)
                    return
                stage = wf.stage(target)

                if stage.hitl:
                    self._event(s, case, run_no, stage, StageStatus.started)
                    self._event(
                        s,
                        case,
                        run_no,
                        stage,
                        StageStatus.waiting,
                        reasoning=["Waiting for a reviewer: approve, remark, reject or request info."],
                        input_summary={"waiting_for": "review"},
                    )
                    self._set_status(s, cfg, case, CaseStatus.needs_review, stage.id)
                    return

                agent_cfg = cfg.agents[stage.agent or ""]
                if not agent_cfg.enabled:
                    self._event(
                        s,
                        case,
                        run_no,
                        stage,
                        StageStatus.skipped,
                        outcome=SKIPPED,
                        reasoning=[f"{agent_cfg.name} is disabled in config; stage skipped."],
                    )
                    target = stage.next[SKIPPED]
                    continue

                case.current_stage = stage.id
                s.commit()
                self._event(s, case, run_no, stage, StageStatus.started)
                result, error, attempts = await self._run_agent(s, cfg, registry, case, stage, run_no)
                if result is None:
                    self._event(
                        s,
                        case,
                        run_no,
                        stage,
                        StageStatus.failed,
                        output={"error": error},
                        reasoning=attempts,
                    )
                    self._set_status(s, cfg, case, CaseStatus.failed, stage.id)
                    return

                metrics = {**result.metrics, "confidence": result.confidence, "attempts": len(attempts) + 1}
                if stage.wait_for:
                    self._event(
                        s,
                        case,
                        run_no,
                        stage,
                        StageStatus.waiting,
                        outcome=result.outcome,
                        output=result.data,
                        metrics=metrics,
                        input_summary={"waiting_for": stage.wait_for},
                        reasoning=[*result.reasoning, f"Waiting for event '{stage.wait_for}'."],
                    )
                    self._set_status(s, cfg, case, CaseStatus.on_hold, stage.id)
                    return

                target, note = self._next_target(s, cfg, case, stage, result.outcome)
                self._event(
                    s,
                    case,
                    run_no,
                    stage,
                    StageStatus.completed,
                    outcome=result.outcome,
                    output=result.data,
                    metrics=metrics,
                    reasoning=[*attempts, *result.reasoning, *note],
                )
                for child_id in result.spawn:  # e.g. one case per invoice of a bundle
                    self.enqueue(child_id)
            raise RuntimeError(
                f"case {case.ref}: more than {max_steps} stages in one run (loop in workflow?)"
            )

    def _complete_paused(self, s: Session, cfg: ResolvedConfig, case: Case, run_no: int, r: Resume) -> str:
        stage = cfg.workflow.stage(r.stage_id)
        if stage.hitl:
            assert r.outcome is not None
            reasoning = [f"Reviewer {r.actor or 'unknown'} chose '{r.outcome}'."]
            if r.text:
                reasoning.append(f"Remark: {r.text}")
            target, note = self._next_target(s, cfg, case, stage, r.outcome)
            self._event(
                s,
                case,
                run_no,
                stage,
                StageStatus.completed,
                outcome=r.outcome,
                input_summary={"review_action": r.outcome, "text": r.text, "actor": r.actor},
                reasoning=[*reasoning, *note],
            )
            return target
        waiting = s.scalars(
            select(StageEvent)
            .where(
                StageEvent.case_id == case.id,
                StageEvent.stage_id == stage.id,
                StageEvent.status == StageStatus.waiting,
            )
            .order_by(StageEvent.seq.desc())
        ).first()
        assert stage.resume_to is not None
        self._event(
            s,
            case,
            run_no,
            stage,
            StageStatus.completed,
            outcome=waiting.outcome if waiting else None,
            input_summary={"event": r.event},
            reasoning=[f"Event '{r.event}' received; resuming at '{stage.resume_to}'."],
        )
        return stage.resume_to

    def _next_target(
        self, s: Session, cfg: ResolvedConfig, case: Case, stage: Stage, outcome: str
    ) -> tuple[str, list[str]]:
        target = stage.next[outcome]
        opts = cfg.workflow.options
        redirect = opts.sticky_review_redirect.get(outcome)
        if opts.sticky_review and redirect and redirect != target and self._was_reviewed(s, case.id):
            return redirect, [
                (
                    f"Sticky review: a reviewer already handled this case, so '{outcome}' goes to "
                    f"'{redirect}' instead of '{target}'."
                )
            ]
        return target, []

    def _was_reviewed(self, s: Session, case_id: int) -> bool:
        n = s.scalar(
            select(func.count()).select_from(ReviewActionRow).where(ReviewActionRow.case_id == case_id)
        )
        return bool(n)

    async def _run_agent(
        self,
        s: Session,
        cfg: ResolvedConfig,
        registry: dict[str, Agent],
        case: Case,
        stage: Stage,
        run_no: int,
    ) -> tuple[AgentResult | None, str | None, list[str]]:
        """Run with retries and a timeout. Returns (result, error, retry notes)."""
        agent_cfg = cfg.agents[stage.agent or ""]
        agent = registry[agent_cfg.id]
        action = stage.action or ""
        declared = agent_cfg.actions[action].outcomes
        retries = stage.retries if stage.retries is not None else agent_cfg.retries
        timeout = stage.timeout_s or agent_cfg.timeout_s or cfg.app.limits.stage_timeout_s

        async def emit(msg: str) -> None:
            self.bus.publish(case.id, "progress", {"stage_id": stage.id, "message": msg})

        notes: list[str] = []
        error = "unknown error"
        for attempt in range(retries + 1):
            ctx = AgentContext(
                case_id=case.id,
                case_ref=case.ref,
                stage_id=stage.id,
                run_no=run_no,
                config=cfg,
                agent_config=agent_cfg,
                state=fold_state(s, case.id),
                session_factory=self.session_factory,
                llm=self.llm,
                storage=self.storage,
                emit=emit,
            )
            t0 = time.perf_counter()
            try:
                result = await asyncio.wait_for(agent.run(action, ctx), timeout=timeout)
                if result.outcome not in declared:
                    raise ValueError(
                        f"returned outcome '{result.outcome}', not one of {declared} declared in agents/{agent_cfg.id}.yaml"
                    )
                result.metrics.setdefault("latency_ms", round((time.perf_counter() - t0) * 1000))
                return result, None, notes
            except TimeoutError:
                error = f"timed out after {timeout:g}s"
            except Exception as e:  # agent bug or upstream failure: retried, then the stage fails
                error = f"{type(e).__name__}: {e}"
                log.warning("case %s stage %s: agent raised", case.ref, stage.id, exc_info=True)
            notes.append(f"Attempt {attempt + 1} of {retries + 1} failed: {error}")
        return None, error, notes

    # ---------------- persistence helpers
    def _event(
        self,
        s: Session,
        case: Case,
        run_no: int,
        stage: Stage,
        status: StageStatus,
        *,
        outcome: str | None = None,
        output: dict[str, Any] | None = None,
        reasoning: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
        input_summary: dict[str, Any] | None = None,
    ) -> StageEvent:
        seq = (s.scalar(select(func.max(StageEvent.seq)).where(StageEvent.case_id == case.id)) or 0) + 1
        now = utcnow()
        started_at = now
        if status != StageStatus.started:
            started = s.scalars(
                select(StageEvent)
                .where(
                    StageEvent.case_id == case.id,
                    StageEvent.stage_id == stage.id,
                    StageEvent.status == StageStatus.started,
                )
                .order_by(StageEvent.seq.desc())
            ).first()
            if started is not None and status != StageStatus.skipped:
                started_at = started.started_at
        metrics = dict(metrics or {})
        if status != StageStatus.started:
            metrics.setdefault("duration_ms", round((now - started_at).total_seconds() * 1000))
        ev = StageEvent(
            case_id=case.id,
            run_no=run_no,
            seq=seq,
            stage_id=stage.id,
            agent_id=stage.agent,
            action=stage.action,
            status=status,
            outcome=outcome,
            input_summary=input_summary or {},
            output=output or {},
            reasoning=(reasoning or [])[:20],
            metrics=metrics,
            started_at=started_at,
            ended_at=None if status == StageStatus.started else now,
        )
        s.add(ev)
        s.commit()
        self.bus.publish(case.id, "stage_event", event_to_dict(ev))
        return ev

    def _set_status(
        self, s: Session, cfg: ResolvedConfig, case: Case, status: CaseStatus, stage_id: str
    ) -> None:
        case.status = status
        case.current_stage = stage_id
        if status in RELEASE_ON:  # a rejected or failed case gives its PO reservation back
            po_ledger.release(s, case.id)
        sla = cfg.app.app.sla_hours.get(status.value)
        case.sla_due_at = utcnow() + timedelta(hours=sla) if sla else None
        s.commit()
        self.bus.publish(case.id, "case_updated", case_summary(case))

    def _mark_failed(self, case_id: int, error: str) -> None:
        """Orchestrator-level crash: still append a failed stage_event so the UI can show the error."""
        with self.session_factory() as s:
            case = s.get(Case, case_id)
            if case is None:
                return
            stage_id = case.current_stage or "orchestrator"
            last = s.scalars(
                select(StageEvent).where(StageEvent.case_id == case_id).order_by(StageEvent.seq.desc())
            ).first()
            now = utcnow()
            ev = StageEvent(
                case_id=case_id,
                run_no=last.run_no if last else 1,
                seq=(last.seq if last else 0) + 1,
                stage_id=stage_id,
                agent_id=last.agent_id if last and last.stage_id == stage_id else None,
                action=last.action if last and last.stage_id == stage_id else None,
                status=StageStatus.failed,
                output={"error": error},
                reasoning=[f"The orchestrator stopped this run: {error}"],
                started_at=now,
                ended_at=now,
            )
            s.add(ev)
            case.status = CaseStatus.failed
            po_ledger.release(s, case_id)
            s.commit()
            self.bus.publish(case_id, "stage_event", event_to_dict(ev))
            self.bus.publish(case_id, "case_updated", case_summary(case))

    @staticmethod
    def _get_case(s: Session, case_id: int) -> Case:
        case = s.get(Case, case_id)
        if case is None:
            raise OrchestratorError(f"case {case_id} not found")
        return case

    @staticmethod
    def _stage_or_none(cfg: ResolvedConfig, stage_id: str | None) -> Stage | None:
        try:
            return cfg.workflow.stage(stage_id) if stage_id else None
        except KeyError:
            return None
