"""LLM adapter: strict-schema post-processing, the Azure call shape (with a fake client), FakeLLM matching."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.config_loader import build_config
from app.llm import AzureLLM, FakeLLM, LLMError, LLMRequest, image_part, strict_json_schema, text_part
from app.llm_schemas import SCHEMAS, ChangeSet, ClassifierOutput, DecisionExplanation, InvoiceExtraction
from app.models import LLMCache
from app.settings import BACKEND_DIR
from tests.conftest import patch_yaml

LLM_DIR = BACKEND_DIR / "tests" / "llm_fixtures"


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_strict_schema_rules(name: str):
    schema = strict_json_schema(SCHEMAS[name])
    for node in _walk(schema):
        if "properties" in node:
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"]), f"{name}: every property must be required"
        for banned in ("oneOf", "discriminator", "default", "minimum", "maximum", "format", "const"):
            assert banned not in node, f"{name}: {banned} left in schema"


def test_changeset_union_becomes_anyof_with_enum_tags():
    schema = strict_json_schema(ChangeSet)
    ops = schema["properties"]["ops"]["items"]
    assert "anyOf" in ops and len(ops["anyOf"]) == 8
    set_field = schema["$defs"]["SetFieldOp"]["properties"]["op"]
    assert set_field["enum"] == ["set_field"]


def test_nullable_fields_stay_nullable():
    props = strict_json_schema(InvoiceExtraction)["properties"]
    assert {"type": "null"} in props["invoice_number"]["anyOf"]
    assert {"type": "number"} in props["total"]["anyOf"]


# ---------------------------------------------------------------- AzureLLM with a fake client
class FakeResponses:
    def __init__(self, replies: list[Any]):
        self.replies = replies
        self.kwargs: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        return self.replies.pop(0)


def _response(payload: dict[str, Any] | None, *, status: str = "completed", tin: int = 1000, tout: int = 200):
    return SimpleNamespace(
        status=status,
        incomplete_details=SimpleNamespace(reason="max_output_tokens") if status == "incomplete" else None,
        output_text=json.dumps(payload) if payload is not None else "",
        usage=SimpleNamespace(
            input_tokens=tin, output_tokens=tout, input_tokens_details=SimpleNamespace(cached_tokens=100)
        ),
    )


def _cfg(config_files, *, cache: bool = False):
    files = patch_yaml(
        config_files,
        "app.yaml",
        lambda d: (
            d["llm"].update(pricing_per_1m_usd={"input": 2.0, "cached_input": 0.5, "output": 10.0}),
            d["features"].update(llm_cache=cache),
        ),
    )
    return build_config(files)


def _req(**kw: Any) -> LLMRequest:
    base = {
        "agent": "decision",
        "action": "decide",
        "schema": DecisionExplanation,
        "instructions": "Explain.",
        "content": [text_part("rules..."), image_part(b"\x89PNG fake")],
        "effort": "low",
        "max_output_tokens": 1000,
    }
    return LLMRequest(**{**base, **kw})


def _azure(replies: list[Any], session_factory=None) -> tuple[AzureLLM, FakeResponses]:
    llm = AzureLLM(
        "https://example.openai.azure.com",
        "k",
        "gpt-5.2",
        api_path="responses",
        session_factory=session_factory,
    )
    fake = FakeResponses(replies)
    llm.client = SimpleNamespace(responses=fake)
    return llm, fake


OK = {"summary": "All checks passed.", "next_step": "Post it."}


async def test_responses_call_shape_metrics_and_cost(config_files):
    llm, fake = _azure([_response(OK)])
    parsed, m = await llm.structured(_req(), _cfg(config_files))
    assert parsed == DecisionExplanation(**OK)
    kw = fake.kwargs[0]
    for banned in ("temperature", "top_p", "max_tokens"):
        assert banned not in kw
    assert kw["reasoning"] == {"effort": "low"} and kw["max_output_tokens"] == 1000
    fmt = kw["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"] == "DecisionExplanation"
    parts = kw["input"][0]["content"]
    assert parts[0] == {"type": "input_text", "text": "rules..."}
    assert parts[1]["type"] == "input_image" and parts[1]["image_url"].startswith("data:image/png;base64,")
    # 900 fresh x $2 + 100 cached x $0.5 + 200 out x $10 per 1M
    assert m["cost_usd"] == pytest.approx((900 * 2 + 100 * 0.5 + 200 * 10) / 1e6)
    assert m["tokens_in"] == 1000 and m["tokens_out"] == 200 and m["cache_hit"] is False


async def test_incomplete_is_retried_once_with_double_budget(config_files):
    llm, fake = _azure([_response(None, status="incomplete"), _response(OK)])
    _, m = await llm.structured(_req(), _cfg(config_files))
    assert [k["max_output_tokens"] for k in fake.kwargs] == [1000, 2000]
    assert m["retried_incomplete"] is True


async def test_incomplete_twice_raises(config_files):
    llm, _ = _azure([_response(None, status="incomplete"), _response(None, status="incomplete")])
    with pytest.raises(LLMError, match="incomplete"):
        await llm.structured(_req(), _cfg(config_files))


async def test_schema_mismatch_raises(config_files):
    llm, _ = _azure([_response({"summary": "x"})])
    with pytest.raises(LLMError, match="does not match"):
        await llm.structured(_req(), _cfg(config_files))


async def test_cache_hit_skips_the_call(config_files, session_factory):
    llm, fake = _azure([_response(OK)], session_factory)
    cfg = _cfg(config_files, cache=True)
    await llm.structured(_req(), cfg)
    parsed, m = await llm.structured(_req(), cfg)
    assert len(fake.kwargs) == 1 and m["cache_hit"] is True and parsed.summary == OK["summary"]
    with session_factory() as s:
        assert s.query(LLMCache).count() == 1


async def test_chat_fallback_shape(config_files):
    llm = AzureLLM("https://example.openai.azure.com", "k", "gpt-5.2", api_path="chat")
    seen: dict[str, Any] = {}

    async def create(**kwargs: Any) -> Any:
        seen.update(kwargs)
        msg = SimpleNamespace(content=json.dumps(OK))
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], usage=usage)

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    _, m = await llm.structured(_req(), _cfg(config_files))
    assert "max_tokens" not in seen and "temperature" not in seen
    assert seen["max_completion_tokens"] == 1000 and seen["reasoning_effort"] == "low"
    assert seen["response_format"]["json_schema"]["strict"] is True
    assert seen["messages"][1]["content"][1]["type"] == "image_url"
    assert m["path"] == "chat" and m["tokens_in"] == 10


# ---------------------------------------------------------------- FakeLLM
async def test_fake_llm_matches_document_and_when(config_files):
    fake = FakeLLM(LLM_DIR)
    cfg = build_config(config_files)
    common = {
        "agent": "extraction",
        "action": "extract",
        "schema": InvoiceExtraction,
        "instructions": "",
        "content": [],
    }
    req = LLMRequest(
        **common,
        effort="low",
        max_output_tokens=1,
        context={"document": "S10_bundle_two_invoices.pdf", "pages": [2, 2]},
    )
    out, _ = await fake.structured(req, cfg)
    assert out.invoice_number == "CL-781"
    req.context["pages"] = [1, 1]
    out, _ = await fake.structured(req, cfg)
    assert out.invoice_number == "INV-A-2240"
    req.context = {"document": "S01_acme_INV-A-2207.pdf"}
    out, _ = await fake.structured(req, cfg)
    assert out.invoice_number == "INV-A-2207"

    w9 = LLMRequest(
        agent="classifier",
        action="classify",
        schema=ClassifierOutput,
        instructions="",
        content=[],
        effort="low",
        max_output_tokens=1,
        context={"document": "S05_novaprint_W9.pdf"},
    )
    out, _ = await fake.structured(w9, cfg)
    assert out.documents[0].label == "tax_form"

    with pytest.raises(LLMError, match="no fixture"):
        req.context = {"document": "unknown.pdf"}
        await fake.structured(req, cfg)
