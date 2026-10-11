from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config_loader import (
    ConfigError,
    build_config,
    load_overrides,
    reset_override,
    resolved_config,
    save_override,
)
from tests.conftest import CONFIG_DIR, patch_yaml


def test_real_config_loads_and_every_outcome_is_mapped(config_files):
    cfg = build_config(config_files)
    assert len(cfg.agents) == 11
    assert cfg.workflow.start == "intake"
    for st in cfg.workflow.stages:
        for outcome in cfg.agent_outcomes(st):
            assert outcome in st.next, f"{st.id} does not map {outcome}"
    assert len(cfg.hash) == 64
    assert "prompts/extraction.md" in cfg.prompts


def test_broken_copy_with_unmapped_outcome_is_rejected(tmp_path: Path):
    broken = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, broken)
    wf = broken / "workflow.yaml"
    wf.write_text(
        wf.read_text(encoding="utf-8").replace(
            'next: { has_invoice: extract, low_confidence: extract, no_invoice: "end:unsupported", split: "end:split" }',
            'next: { has_invoice: extract, low_confidence: extract, split: "end:split" }',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc:
        resolved_config(broken)
    msg = str(exc.value)
    assert "stage 'classify'" in msg
    assert "unmapped outcome of classifier.classify 'no_invoice'" in msg


def test_hash_is_stable_and_changes_with_content(config_files):
    a = build_config(config_files).hash
    assert build_config(dict(config_files)).hash == a
    changed = patch_yaml(config_files, "rules.yaml", lambda d: d["rules"][0].update(enabled=False))
    assert build_config(changed).hash != a


@pytest.mark.parametrize(
    ("path", "mutate", "expected"),
    [
        (
            "workflow.yaml",
            lambda d: d["stages"][1]["next"].update(has_invoice="nowhere"),
            "target 'nowhere' is not a stage id",
        ),
        (
            "workflow.yaml",
            lambda d: d["stages"][0]["next"].update(empty="end:lost"),
            "unknown terminal status 'end:lost'",
        ),
        (
            "workflow.yaml",
            lambda d: d["stages"].append(
                {"id": "orphan", "agent": "comms", "action": "notify", "next": {"ok": "end:approved"}}
            ),
            "stage 'orphan' is unreachable",
        ),
        (
            "workflow.yaml",
            lambda d: d["stages"][1]["next"].update(maybe="extract"),
            "`next` maps 'maybe', which is not a declared outcome",
        ),
        (
            "workflow.yaml",
            lambda d: d["stages"][1].update(agent="ghost"),
            "unknown agent 'ghost'",
        ),
        (
            "workflow.yaml",
            lambda d: next(s for s in d["stages"] if s["id"] == "review")["next"].pop("request_info"),
            "unmapped review action 'request_info'",
        ),
        (
            "workflow.yaml",
            lambda d: d["options"]["sticky_review_redirect"].update(auto_aprove="template_draft"),
            "'auto_aprove' is not an outcome any stage maps",
        ),
        (
            "agents/classifier.yaml",
            lambda d: d["actions"]["classify"].update(prompt="prompts/missing.md"),
            "missing prompt file 'prompts/missing.md'",
        ),
        (
            "agents/policy.yaml",
            lambda d: d.update(enabled=False),
            "agent 'policy' is disabled, so the stage returns 'skipped'",
        ),
        (
            "rules.yaml",
            lambda d: d["rules"][0].update(explain="{{ missing | join(', ') "),
            "rule 'required_fields' explain: template syntax error",
        ),
    ],
)
def test_invalid_configs_are_rejected_with_a_clear_message(config_files, path, mutate, expected):
    with pytest.raises(ConfigError) as exc:
        build_config(patch_yaml(config_files, path, mutate))
    assert expected in str(exc.value)


def test_bare_on_key_is_explained(config_files):
    text = config_files["workflow.yaml"].replace("next: { ok: vendor_check }", "on: { ok: vendor_check }")
    with pytest.raises(ConfigError) as exc:
        build_config({**config_files, "workflow.yaml": text})
    assert "found key `on`" in str(exc.value)


def test_db_override_is_validated_before_save_and_can_be_reset(session_factory, config_files):
    with session_factory() as s:
        base = resolved_config(CONFIG_DIR, s)
        bad = config_files["workflow.yaml"].replace('no_invoice: "end:unsupported"', "")
        with pytest.raises(ConfigError):
            save_override(s, CONFIG_DIR, "workflow.yaml", bad, "admin")
        assert load_overrides(s) == {}  # nothing half-applied

        good = patch_yaml(config_files, "rules.yaml", lambda d: d["rules"][9]["params"].update(pct=5.0))
        cfg = save_override(s, CONFIG_DIR, "rules.yaml", good["rules.yaml"], "admin")
        assert cfg.hash != base.hash
        assert resolved_config(CONFIG_DIR, s).rules.rules[9].params["pct"] == 5.0

        reset_override(s, "rules.yaml")
        assert resolved_config(CONFIG_DIR, s).hash == base.hash
