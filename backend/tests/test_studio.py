"""Phase F3: Agent Studio API (files, validate-before-save, versions, reset, agent stats, test-agent dry run)
and the done-check: price_tolerance.pct = 5 makes a new S08 run auto-approve."""

from __future__ import annotations

from datetime import date

import pytest
import yaml
from sqlalchemy import func, select

from app.agents import policy
from app.main import create_app
from app.models import ConfigVersion, POConsumption, RuleResult, StageEvent, TemplateVersion
from app.settings import Settings
from tests.conftest import auth_client
from tests.test_api import wait_status

SETTLED = {"approved", "needs_review", "on_hold", "rejected", "failed"}


def with_pct(rules_yaml: str, pct: float) -> str:
    data = yaml.safe_load(rules_yaml)
    for r in data["rules"]:
        if r["id"] == "price_tolerance":
            r["params"]["pct"] = pct
    return yaml.safe_dump(data, sort_keys=False)


@pytest.fixture
def live_settings(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(policy, "today", lambda: date(2026, 10, 11))
    return test_settings.model_copy(update={"force_stub_agents": False, "llm_backend": "fake"})


def test_files_validate_save_versions_reset(test_settings: Settings):
    with auth_client(create_app(test_settings)) as client:
        files = {f["path"]: f for f in client.get("/api/config/files").json()}
        assert {"rules.yaml", "workflow.yaml", "agents/policy.yaml", "prompts/extraction.md"} <= set(files)
        assert files["agents/policy.yaml"]["kind"] == "agent" and files["prompts/extraction.md"]["kind"] == "prompt"
        assert not files["rules.yaml"]["overridden"]

        rules = client.get("/api/config/rules.yaml").json()
        assert rules["content"] == rules["file_content"] and rules["versions"] == []
        assert client.get("/api/config/nope.yaml").status_code == 404

        # invalid edits: readable errors, nothing saved
        bad = rules["content"].replace("severity: review", "severity: maybe", 1)
        v = client.post("/api/config/validate", json={"edits": {"rules.yaml": bad}}).json()
        assert not v["ok"] and any("severity" in e for e in v["errors"])
        r = client.put("/api/config/rules.yaml", json={"content": bad})
        assert r.status_code == 422 and r.json()["detail"]["errors"]
        broken = client.put("/api/config/rules.yaml", json={"content": "rules: [\n"})
        assert broken.status_code == 422 and "YAML syntax error" in broken.json()["detail"]["errors"][0]
        assert client.get("/api/config/rules.yaml").json()["versions"] == []

        # a disabled agent whose stage does not map `skipped` is rejected with the loader's message
        agent = client.get("/api/config/agents/po_matching.yaml").json()["content"]
        off = agent.replace("enabled: true", "enabled: false", 1)
        assert off != agent
        r = client.put("/api/config/agents/po_matching.yaml", json={"content": off})
        assert r.status_code == 422  # gl_coding does not map `skipped`
        assert any("stage 'gl_coding'" in e and "skipped" in e for e in r.json()["detail"]["errors"])
        assert not client.get("/api/config/agents/po_matching.yaml").json()["overridden"]

        # a misspelt handler would silently run a stub: rejected
        policy_yaml = client.get("/api/config/agents/policy.yaml").json()["content"]
        typo = policy_yaml.replace("app.agents.policy:PolicyAgent", "app.agents.polcy:PolicyAgent")
        r = client.put("/api/config/agents/policy.yaml", json={"content": typo})
        assert r.status_code == 422 and "module not found" in r.json()["detail"]["errors"][0]
        wrong_cls = policy_yaml.replace("app.agents.policy:PolicyAgent", "app.agents.policy:Nope")
        r = client.put("/api/config/agents/policy.yaml", json={"content": wrong_cls})
        assert r.status_code == 422 and "AttributeError" in r.json()["detail"]["errors"][0]
        assert not client.post("/api/config/validate", json={"edits": {"agents/policy.yaml": typo}}).json()["ok"]

        # a valid edit becomes the active version and the resolved config
        good = with_pct(rules["content"], 5)
        hash0 = client.get("/api/config").json()["hash"]
        base_hash = client.get("/healthz").json()["config_hash"]
        saved = client.put("/api/config/rules.yaml", json={"content": good, "author": "Admin"})
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["overridden"] and body["active_version"] == 1 and body["config_hash"] != hash0
        assert client.get("/api/config").json()["hash"] == body["config_hash"]  # this user's resolved config
        assert client.get("/healthz").json()["config_hash"] == base_hash  # the server's baseline is untouched
        resolved = client.get("/api/config").json()
        price = next(r for r in resolved["rules"]["rules"] if r["id"] == "price_tolerance")
        assert price["params"]["pct"] == 5

        again = client.put("/api/config/rules.yaml", json={"content": with_pct(rules["content"], 3)}).json()
        assert [(x["version"], x["active"]) for x in again["versions"]] == [(2, True), (1, False)]

        reset = client.delete("/api/config/rules.yaml").json()
        assert not reset["overridden"] and reset["content"] == rules["file_content"]
        assert [x["active"] for x in reset["versions"]] == [False, False]  # history kept
        assert reset["config_hash"] == hash0
        assert client.delete("/api/config/nope.yaml").status_code == 404


def test_s08_flips_to_auto_approve_with_pct_5_and_test_agent_is_dry(live_settings: Settings):
    with auth_client(create_app(live_settings)) as client:
        first = client.post("/api/fixtures/S08_price_variance/run").json()
        first = wait_status(client, first["id"], SETTLED, timeout=60)
        assert first["decision"] == "needs_review"
        fails = [r["rule_id"] for r in client.get(f"/api/cases/{first['id']}").json()["rule_results"] if r["status"] == "fail"]
        assert fails == ["price_tolerance"]

        # dry run of the policy agent with an unsaved pct=5 edit: price_tolerance passes, nothing is stored
        sf = client.app.state.session_factory
        rules = client.get("/api/config/rules.yaml").json()["content"]

        def counts() -> tuple[int, ...]:
            with sf() as s:
                return tuple(
                    s.scalar(select(func.count()).select_from(m))
                    for m in (RuleResult, StageEvent, POConsumption, TemplateVersion, ConfigVersion)
                )

        before = counts()
        dry = client.post(
            "/api/config/test-agent",
            json={"agent": "policy", "action": "evaluate", "case_id": first["id"], "edits": {"rules.yaml": with_pct(rules, 5)}},
        )
        assert dry.status_code == 200, dry.text
        out = dry.json()
        assert out["ok"] and out["outcome"] == "evaluated" and out["data"]["failed"] == [], out
        price = next(r for r in out["data"]["results"] if r["rule_id"] == "price_tolerance")
        assert price["status"] == "pass"
        assert counts() == before
        detail = client.get(f"/api/cases/{first['id']}").json()
        assert [r["rule_id"] for r in detail["rule_results"] if r["status"] == "fail"] == ["price_tolerance"]

        bad = client.post("/api/config/test-agent", json={"agent": "policy", "action": "nope", "case_id": first["id"]})
        assert bad.status_code == 422
        assert client.post("/api/config/test-agent", json={"agent": "x", "action": "y", "case_id": first["id"]}).status_code == 404

        # close the first S08 (a live one would make the re-run a duplicate), save pct=5, run S08 again
        r = client.post(f"/api/cases/{first['id']}/review", json={"action": "reject", "text": "Re-running with a new tolerance"})
        assert r.status_code == 200, r.text
        wait_status(client, first["id"], {"rejected"}, timeout=30)
        assert client.put("/api/config/rules.yaml", json={"content": with_pct(rules, 5)}).status_code == 200

        second = client.post("/api/fixtures/S08_price_variance/run").json()
        second = wait_status(client, second["id"], SETTLED, timeout=60)
        assert (second["decision"], second["status"]) == ("auto_approve", "approved"), second
        assert second["config_hash"] != first["config_hash"]

        stats = client.get("/api/config/agent-stats").json()
        assert stats["policy"]["runs"] >= 2 and stats["policy"]["avg_latency_ms"] is not None

        # set it back
        assert client.delete("/api/config/rules.yaml").json()["overridden"] is False
        price = next(r for r in client.get("/api/config").json()["rules"]["rules"] if r["id"] == "price_tolerance")
        assert price["params"]["pct"] == 2.0

        po = next(p for p in client.get("/api/pos").json() if p["po_number"] == "PO-1003")
        consumption = client.get(f"/api/pos/{po['id']}").json()["consumption"]
        assert second["ref"] in {c["case_ref"] for c in consumption}
        lib = client.get("/api/approved").json()["items"]
        assert next(c for c in lib if c["id"] == second["id"])["latest_template_version"] == 1
