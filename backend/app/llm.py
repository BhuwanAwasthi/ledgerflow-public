"""LLM adapter (SPEC section 8): Azure OpenAI GPT-5.2 with strict JSON-schema outputs, plus FakeLLM for tests.

Every call returns `(parsed, metrics)`. The model only perceives and explains: callers validate the parsed
Pydantic object and decide in code.

Azure gotchas handled here: never send temperature/top_p/max_tokens; reasoning effort + max_output_tokens
(Responses) or max_completion_tokens (Chat); strict schemas need every property required and
additionalProperties false; `status == "incomplete"` is retried once with double the token budget.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from openai import AsyncOpenAI, OpenAIError
from openai.types.chat import ChatCompletionContentPartParam
from openai.types.responses import ResponseFormatTextJSONSchemaConfigParam, ResponseInputContentParam
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.config_loader import ReasoningEffort, ResolvedConfig
from app.models import LLMCache
from app.settings import Settings

log = logging.getLogger(__name__)

Part = dict[str, Any]  # neutral content part: {"type": "text", "text"} | {"type": "image", "png": bytes}


class LLMError(RuntimeError):
    pass


def text_part(text: str) -> Part:
    return {"type": "text", "text": text}


def image_part(png: bytes) -> Part:
    return {"type": "image", "png": png}


# ---------------------------------------------------------------- strict schema
_DROP_KEYS = {
    "default",
    "title",
    "discriminator",
    "format",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
}


def _strictify(node: Any) -> Any:
    if isinstance(node, list):
        return [_strictify(n) for n in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k in _DROP_KEYS:  # property names are handled by the `properties` branch, never dropped
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {name: _strictify(sub) for name, sub in v.items()}
        elif k == "oneOf":
            out["anyOf"] = _strictify(v)
        elif k == "const":
            out["enum"] = [v]
        else:
            out[k] = _strictify(v)
    if out.get("type") == "object" or "properties" in out:
        props = out.get("properties", {})
        out["required"] = list(props)
        out["additionalProperties"] = False
    return out


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema rewritten for OpenAI strict mode."""
    return _strictify(model.model_json_schema())


def schema_hash(schema: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------- request / metrics
@dataclass
class LLMRequest:
    agent: str
    action: str
    schema: type[BaseModel]
    instructions: str
    content: list[Part]
    effort: ReasoningEffort
    max_output_tokens: int
    deployment: str | None = None
    json_schema: dict[str, Any] | None = None  # override (e.g. label enum injected from config)
    context: dict[str, Any] = field(default_factory=dict)  # fixture match keys (document, pages, ...)

    @property
    def schema_dict(self) -> dict[str, Any]:
        return self.json_schema or strict_json_schema(self.schema)


class LLM(Protocol):
    path: str

    async def structured(self, req: LLMRequest, cfg: ResolvedConfig) -> tuple[BaseModel, dict[str, Any]]: ...


def cost_usd(cfg: ResolvedConfig, tokens_in: int, tokens_out: int, cached_in: int = 0) -> float:
    p = cfg.app.llm.pricing_per_1m_usd
    fresh = max(tokens_in - cached_in, 0)
    return round(
        (
            fresh * p.get("input", 0.0)
            + cached_in * p.get("cached_input", 0.0)
            + tokens_out * p.get("output", 0.0)
        )
        / 1_000_000,
        6,
    )


def _parse(req: LLMRequest, raw: str | dict[str, Any]) -> BaseModel:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return req.schema.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        raise LLMError(
            f"{req.agent}.{req.action}: model output does not match {req.schema.__name__}: {e}"
        ) from e


# ---------------------------------------------------------------- Azure OpenAI
_PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class AzureLLM:
    """Azure OpenAI v1 endpoint. `path` is "responses" or "chat" once decided (startup probe or config)."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        deployment: str,
        *,
        api_path: str = "auto",
        timeout_s: float = 120,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.client = AsyncOpenAI(
            base_url=f"{endpoint.rstrip('/')}/openai/v1/", api_key=api_key, timeout=timeout_s
        )
        self.deployment = deployment
        self.path = api_path if api_path in ("responses", "chat") else "not_probed"
        self.session_factory = session_factory
        self._probe_lock = asyncio.Lock()

    # ---------- path selection (smoke_azure.py logic)
    async def probe(self) -> str:
        async with self._probe_lock:
            if self.path in ("responses", "chat"):
                return self.path
            fmt: ResponseFormatTextJSONSchemaConfigParam = {
                "type": "json_schema", "name": "probe", "schema": _PROBE_SCHEMA, "strict": True
            }
            try:
                await self.client.responses.create(
                    model=self.deployment,
                    input="Reply with ok=true.",
                    reasoning={"effort": "low"},
                    max_output_tokens=500,
                    text={"format": fmt},
                )
                self.path = "responses"
            except OpenAIError as e:  # API or connection error: try the chat fallback
                log.warning("llm probe: Responses API unavailable (%s); trying chat", type(e).__name__)
                try:
                    await self.client.chat.completions.create(
                        model=self.deployment,
                        messages=[{"role": "user", "content": "Reply with ok=true."}],
                        reasoning_effort="low",
                        max_completion_tokens=500,
                        response_format={
                            "type": "json_schema",
                            "json_schema": {"name": "probe", "schema": _PROBE_SCHEMA, "strict": True},
                        },
                    )
                    self.path = "chat"
                except OpenAIError as e2:
                    log.error("llm probe: chat path failed too (%s)", type(e2).__name__)
                    self.path = "unavailable"
            log.info("llm path: %s", self.path)
            return self.path

    # ---------- cache
    def _cache_key(self, req: LLMRequest, model: str) -> str:
        h = hashlib.sha256()
        for piece in (model, req.effort, req.instructions, schema_hash(req.schema_dict)):
            h.update(piece.encode())
            h.update(b"\0")
        for p in req.content:
            h.update(hashlib.sha256(p["png"] if p["type"] == "image" else p["text"].encode()).digest())
        return h.hexdigest()

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        if self.session_factory is None:
            return None
        with self.session_factory() as s:
            row = s.get(LLMCache, key)
            return row.response if row else None

    def _cache_put(self, key: str, response: dict[str, Any]) -> None:
        if self.session_factory is None:
            return
        with self.session_factory() as s:
            s.merge(LLMCache(key=key, response=response))
            s.commit()

    # ---------- call
    async def structured(self, req: LLMRequest, cfg: ResolvedConfig) -> tuple[BaseModel, dict[str, Any]]:
        model = req.deployment or self.deployment
        use_cache = cfg.app.features.get("llm_cache", False)
        key = self._cache_key(req, model) if use_cache else None
        t0 = time.perf_counter()
        if key and (hit := self._cache_get(key)) is not None:
            parsed = _parse(req, hit["output"])
            return parsed, {
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "cache_hit": True,
                "path": "cache",
            }

        if self.path not in ("responses", "chat"):
            await self.probe()
        if self.path == "unavailable":
            raise LLMError("Azure OpenAI is unavailable (neither the Responses nor the Chat API answered)")

        budget = req.max_output_tokens
        retried = False
        while True:
            if self.path == "responses":
                raw, usage, incomplete = await self._responses(req, model, budget)
            else:
                raw, usage, incomplete = await self._chat(req, model, budget)
            if incomplete and not retried:
                log.warning(
                    "%s.%s: output incomplete at %d tokens; retrying with %d",
                    req.agent,
                    req.action,
                    budget,
                    budget * 2,
                )
                budget *= 2
                retried = True
                continue
            if incomplete:
                raise LLMError(
                    f"{req.agent}.{req.action}: output still incomplete at {budget} max_output_tokens"
                )
            break

        parsed = _parse(req, raw)
        tokens_in, tokens_out, cached = usage
        metrics = {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached_tokens": cached,
            "cost_usd": cost_usd(cfg, tokens_in, tokens_out, cached),
            "latency_ms": round((time.perf_counter() - t0) * 1000),
            "cache_hit": False,
            "path": self.path,
            "retried_incomplete": retried,
        }
        if key:
            self._cache_put(key, {"output": json.loads(raw)})
        return parsed, metrics

    async def _responses(
        self, req: LLMRequest, model: str, budget: int
    ) -> tuple[str, tuple[int, int, int], bool]:
        content: list[ResponseInputContentParam] = [
            {"type": "input_text", "text": p["text"]}
            if p["type"] == "text"
            else {"type": "input_image", "image_url": _data_url(p["png"]), "detail": "auto"}
            for p in req.content
        ]
        r = await self.client.responses.create(
            model=model,
            instructions=req.instructions,
            input=[{"role": "user", "content": content}],
            reasoning={"effort": req.effort},
            max_output_tokens=budget,
            text={
                "format": {
                    "type": "json_schema",
                    "name": req.schema.__name__,
                    "schema": req.schema_dict,
                    "strict": True,
                }
            },
        )
        incomplete = (
            r.status == "incomplete" and getattr(r.incomplete_details, "reason", None) == "max_output_tokens"
        )
        u = r.usage
        cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0 if u else 0
        usage = (u.input_tokens, u.output_tokens, cached) if u else (0, 0, 0)
        if not incomplete and not r.output_text:
            raise LLMError(f"{req.agent}.{req.action}: empty response (status {r.status})")
        return r.output_text, usage, incomplete

    async def _chat(self, req: LLMRequest, model: str, budget: int) -> tuple[str, tuple[int, int, int], bool]:
        content: list[ChatCompletionContentPartParam] = [
            {"type": "text", "text": p["text"]}
            if p["type"] == "text"
            else {"type": "image_url", "image_url": {"url": _data_url(p["png"])}}
            for p in req.content
        ]
        r = await self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "developer", "content": req.instructions},
                {"role": "user", "content": content},
            ],
            reasoning_effort=req.effort,
            max_completion_tokens=budget,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": req.schema.__name__, "schema": req.schema_dict, "strict": True},
            },
        )
        choice = r.choices[0]
        incomplete = choice.finish_reason == "length"
        u = r.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0 if u else 0
        usage = (u.prompt_tokens, u.completion_tokens, cached) if u else (0, 0, 0)
        text = choice.message.content or ""
        if not incomplete and not text:
            raise LLMError(f"{req.agent}.{req.action}: empty response ({choice.finish_reason})")
        return text, usage, incomplete


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


# ---------------------------------------------------------------- FakeLLM
class FakeLLM:
    """Returns the recorded outputs in tests/llm_fixtures/<scenario>__<agent>.json.

    A fixture file applies when the call's `context["document"]` is one of its `documents`. Within it, the
    first call for the action whose `when` keys all equal the context wins; otherwise the first call for
    the action (fallback). `calls` records every request for assertions.
    """

    path = "fake"

    def __init__(self, fixtures_dir: Path, delay_s: float = 0.0) -> None:
        self.delay_s = delay_s
        self.fixtures: list[dict[str, Any]] = [
            json.loads(p.read_text(encoding="utf-8")) for p in sorted(fixtures_dir.glob("*.json"))
        ]
        self.calls: list[LLMRequest] = []

    def find(self, req: LLMRequest) -> dict[str, Any]:
        doc = req.context.get("document")
        for fx in self.fixtures:
            if fx["agent"] != req.agent or doc not in fx["documents"]:
                continue
            calls = [c for c in fx["calls"] if c["action"] == req.action]
            if not calls:
                continue
            for c in calls:
                if all(_same(req.context.get(k), v) for k, v in c["when"].items()):
                    return c
            return calls[0]
        raise LLMError(f"FakeLLM: no fixture for {req.agent}.{req.action} on document {doc!r}")

    async def structured(self, req: LLMRequest, cfg: ResolvedConfig) -> tuple[BaseModel, dict[str, Any]]:
        self.calls.append(req)
        req.schema_dict  # noqa: B018  (build the strict schema so a broken schema fails in tests too)
        call = self.find(req)
        if call["schema"] != req.schema.__name__:
            raise LLMError(f"FakeLLM: fixture schema {call['schema']} != requested {req.schema.__name__}")
        parsed = _parse(req, copy.deepcopy(call["output"]))
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        return parsed, {
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
            "latency_ms": 0,
            "cache_hit": False,
            "path": "fake",
        }


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, tuple | list) and isinstance(b, tuple | list):
        return list(a) == list(b)
    return a == b


def sum_metrics(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Stage metrics from several LLM calls (per-call detail kept under `llm_calls`)."""
    return {
        "tokens_in": sum(c.get("tokens_in", 0) for c in calls),
        "tokens_out": sum(c.get("tokens_out", 0) for c in calls),
        "cost_usd": round(sum(c.get("cost_usd", 0.0) for c in calls), 6),
        "llm_latency_ms": sum(c.get("latency_ms", 0) for c in calls),
        "cache_hit": bool(calls) and all(c.get("cache_hit") for c in calls),
        "llm_calls": calls,
    }


def make_llm(
    settings: Settings, cfg: ResolvedConfig, session_factory: sessionmaker[Session] | None
) -> LLM | None:
    """The app's LLM: FakeLLM when LLM_BACKEND=fake, Azure when configured, else None (LLM stages fail
    with a clear error)."""
    if settings.llm_backend == "fake":
        return FakeLLM(settings.llm_fixtures_dir, settings.fake_llm_delay_s)
    if not (
        settings.azure_openai_endpoint and settings.azure_openai_api_key and settings.azure_openai_deployment
    ):
        return None
    return AzureLLM(
        settings.azure_openai_endpoint,
        settings.azure_openai_api_key,
        settings.azure_openai_deployment,
        api_path=cfg.app.llm.api_path,
        timeout_s=cfg.app.llm.request_timeout_s,
        session_factory=session_factory,
    )
