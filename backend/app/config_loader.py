"""Loads config/ (YAML + Markdown prompts), overlays DB overrides, validates, and hashes.

`build_config(files)` is all-or-nothing: it either returns a fully validated ResolvedConfig or raises
ConfigError listing every problem in plain English. An invalid config is never half-applied.
"""

from __future__ import annotations

import hashlib
from collections import deque
from pathlib import Path
from typing import Any, Literal

import yaml
from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TERMINAL_STATUSES, ConfigVersion, ReviewAction

SKIPPED = "skipped"
END_PREFIX = "end:"
CONFIG_SUFFIXES = (".yaml", ".yml", ".md")


class ConfigError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Invalid config:\n" + "\n".join(f"- {e}" for e in errors))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


# ---------- app.yaml ----------
class AppInfo(_Open):
    name: str
    tagline: str = ""
    company: str = ""
    case_ref_prefix: str
    default_erp_profile: str
    sla_hours: dict[str, float] = Field(default_factory=dict)


class LLMSettings(_Open):
    provider: str
    api_path: Literal["auto", "responses", "chat"] = "auto"
    pricing_per_1m_usd: dict[str, float] = Field(default_factory=dict)
    default_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"
    request_timeout_s: float


class Limits(_Open):
    max_upload_mb: float
    max_pages_per_document: int
    render_dpi: int
    pipeline_concurrency: int = Field(ge=1)
    stage_timeout_s: float = Field(gt=0)
    max_stages_per_run: int = Field(ge=1)


class ExportSettings(_Open):
    batch_ref_prefix: str = "EXP-"
    zip_filename_template: str = "AP_EXPORT_{{ batch.ref }}.zip"


class AppConfig(_Open):
    app: AppInfo
    features: dict[str, bool] = Field(default_factory=dict)
    llm: LLMSettings
    limits: Limits
    exports: ExportSettings = Field(default_factory=ExportSettings)


# ---------- agents/<id>.yaml ----------
class AgentAction(_Strict):
    outcomes: list[str] = Field(min_length=1)
    prompt: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class AgentModel(_Open):
    deployment: str | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None
    max_output_tokens: int | None = None


class AgentConfig(_Strict):
    id: str
    name: str
    description: str = ""
    kind: Literal["llm", "code", "hybrid"]
    enabled: bool = True
    handler: str = Field(pattern=r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
    actions: dict[str, AgentAction] = Field(min_length=1)
    model: AgentModel | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    retries: int = Field(default=0, ge=0)
    timeout_s: float | None = Field(default=None, gt=0)


# ---------- workflow.yaml ----------
class Stage(_Strict):
    id: str
    agent: str | None = None
    action: str | None = None
    status_label: str = ""
    next: dict[str, str] = Field(min_length=1)
    hitl: bool = False
    wait_for: str | None = None
    resume_to: str | None = None
    retries: int | None = Field(default=None, ge=0)
    timeout_s: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _shape(self) -> Stage:
        if self.hitl and (self.agent or self.action):
            raise ValueError(f"stage '{self.id}': a hitl stage has no agent/action")
        if not self.hitl and not (self.agent and self.action):
            raise ValueError(f"stage '{self.id}': needs both `agent` and `action` (or `hitl: true`)")
        if bool(self.wait_for) != bool(self.resume_to):
            raise ValueError(f"stage '{self.id}': `wait_for` and `resume_to` must be set together")
        return self


class WorkflowOptions(_Open):
    sticky_review: bool = False
    # outcome -> stage id used instead of the normal target once a human has reviewed the case
    sticky_review_redirect: dict[str, str] = Field(default_factory=dict)


class Workflow(_Strict):
    process: str
    version: int
    start: str
    options: WorkflowOptions = Field(default_factory=WorkflowOptions)
    stages: list[Stage] = Field(min_length=1)

    def stage(self, stage_id: str) -> Stage:
        for s in self.stages:
            if s.id == stage_id:
                return s
        raise KeyError(stage_id)


# ---------- rules.yaml ----------
Severity = Literal["reject", "hold", "review", "info"]


class Rule(_Strict):
    id: str
    name: str
    enabled: bool = True
    applies_to: Literal["po", "non_po", "all"]
    severity: Severity
    params: dict[str, Any] = Field(default_factory=dict)
    explain: str


class RulesConfig(_Open):
    currency: str
    decision_precedence: list[Severity]
    auto_approve_gates: dict[str, Any] = Field(default_factory=dict)
    rules: list[Rule]


# ---------- document_types.yaml ----------
class DocLabel(_Strict):
    id: str
    description: str
    route: Literal["case_invoice", "evidence", "onboarding", "ignore"]


class DocumentTypes(_Open):
    min_confidence: float = Field(ge=0, le=1)
    labels: list[DocLabel] = Field(min_length=1)


# ---------- erp_profiles/<id>.yaml ----------
class ErpColumn(_Strict):
    header: str
    value: str
    required: bool = False


class ErpBatch(_Open):
    filename_template: str
    mode: str


class ErpProfile(_Open):
    id: str
    name: str
    file_format: Literal["csv", "xlsx"]
    grain: Literal["line", "header"]
    filename_template: str
    date_format: str | None = None
    columns: list[ErpColumn] = Field(min_length=1)
    batch: ErpBatch


# ---------- resolved ----------
class ResolvedConfig(BaseModel):
    app: AppConfig
    workflow: Workflow
    rules: RulesConfig
    document_types: DocumentTypes
    agents: dict[str, AgentConfig]
    erp_profiles: dict[str, ErpProfile]
    prompts: dict[str, str]
    files: dict[str, str]  # path -> raw content: the snapshot stored on each case
    hash: str

    def agent_outcomes(self, stage: Stage) -> list[str]:
        if stage.hitl:
            return [a.value for a in ReviewAction]
        assert stage.agent and stage.action
        return self.agents[stage.agent].actions[stage.action].outcomes

    def public_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"files"})


def config_hash(files: dict[str, str]) -> str:
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode())
        h.update(b"\0")
        h.update(files[path].encode())
        h.update(b"\0")
    return h.hexdigest()


def read_config_files(config_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for p in sorted(config_dir.rglob("*")):
        if p.is_file() and p.suffix in CONFIG_SUFFIXES:
            files[p.relative_to(config_dir).as_posix()] = p.read_text(encoding="utf-8")
    return files


def load_overrides(session: Session) -> dict[str, str]:
    """Latest active DB version per path (Agent Studio edits)."""
    rows = session.scalars(
        select(ConfigVersion).where(ConfigVersion.active.is_(True)).order_by(ConfigVersion.version)
    ).all()
    return {r.path: r.content for r in rows}


def resolved_config(config_dir: Path, session: Session | None = None) -> ResolvedConfig:
    files = read_config_files(config_dir)
    if session is not None:
        files.update(load_overrides(session))
    return build_config(files)


def save_override(session: Session, config_dir: Path, path: str, content: str, author: str) -> ResolvedConfig:
    """Validate the whole config with this edit applied; only then store it as the new active version."""
    files = read_config_files(config_dir)
    files.update(load_overrides(session))
    files[path] = content
    cfg = build_config(files)  # raises ConfigError, nothing saved
    prev = session.scalars(select(ConfigVersion).where(ConfigVersion.path == path)).all()
    for row in prev:
        row.active = False
    version = max((r.version for r in prev), default=0) + 1
    session.add(ConfigVersion(path=path, content=content, version=version, active=True, author=author))
    session.commit()
    return cfg


def reset_override(session: Session, path: str) -> None:
    for row in session.scalars(select(ConfigVersion).where(ConfigVersion.path == path)).all():
        row.active = False
    session.commit()


# ---------- building + validation ----------
def _fmt_pydantic(path: str, err: ValidationError) -> list[str]:
    out = []
    for e in err.errors():
        loc = ".".join(str(x) for x in e["loc"]) or "(root)"
        out.append(f"{path}: {loc}: {e['msg']}")
    return out


def _parse_yaml(path: str, text: str, errors: list[str]) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        errors.append(f"{path}: YAML syntax error: {e}")
        return None


def _model[T: BaseModel](cls: type[T], path: str, raw: Any, errors: list[str]) -> T | None:
    if raw is None:
        if not any(e.startswith(f"{path}:") for e in errors):
            errors.append(f"{path}: file is missing or empty")
        return None
    try:
        return cls.model_validate(raw)
    except ValidationError as e:
        errors.extend(_fmt_pydantic(path, e))
        return None


def _check_template(env: SandboxedEnvironment, where: str, source: str, errors: list[str]) -> None:
    try:
        env.parse(source)
    except TemplateSyntaxError as e:
        errors.append(f"{where}: template syntax error: {e.message}")


def build_config(files: dict[str, str]) -> ResolvedConfig:
    errors: list[str] = []
    jinja = SandboxedEnvironment()

    app = _model(AppConfig, "app.yaml", _parse_yaml("app.yaml", files.get("app.yaml", ""), errors), errors)
    rules = _model(
        RulesConfig, "rules.yaml", _parse_yaml("rules.yaml", files.get("rules.yaml", ""), errors), errors
    )
    doc_types = _model(
        DocumentTypes,
        "document_types.yaml",
        _parse_yaml("document_types.yaml", files.get("document_types.yaml", ""), errors),
        errors,
    )

    raw_wf = _parse_yaml("workflow.yaml", files.get("workflow.yaml", ""), errors)
    if isinstance(raw_wf, dict):
        for i, st in enumerate(raw_wf.get("stages") or []):
            if isinstance(st, dict) and True in st:
                errors.append(
                    f"workflow.yaml: stage #{i + 1} ({st.get('id')}): found key `on` (YAML reads it as true);"
                    " use `next:` for the outcome mapping"
                )
                st.pop(True)
    workflow = _model(Workflow, "workflow.yaml", raw_wf, errors)

    agents: dict[str, AgentConfig] = {}
    erp_profiles: dict[str, ErpProfile] = {}
    prompts = {p: c for p, c in files.items() if p.startswith("prompts/")}
    for path in sorted(files):
        stem = Path(path).stem
        if path.startswith("agents/"):
            agent = _model(AgentConfig, path, _parse_yaml(path, files[path], errors), errors)
            if agent:
                if agent.id != stem:
                    errors.append(f"{path}: id '{agent.id}' must match the file name '{stem}'")
                agents[agent.id] = agent
        elif path.startswith("erp_profiles/"):
            prof = _model(ErpProfile, path, _parse_yaml(path, files[path], errors), errors)
            if prof:
                if prof.id != stem:
                    errors.append(f"{path}: id '{prof.id}' must match the file name '{stem}'")
                erp_profiles[prof.id] = prof
                _check_template(jinja, f"{path}: filename_template", prof.filename_template, errors)
                for col in prof.columns:
                    _check_template(jinja, f"{path}: column '{col.header}'", col.value, errors)

    # prompts referenced by agents must exist
    for agent in agents.values():
        for name, act in agent.actions.items():
            if act.prompt and act.prompt not in prompts:
                errors.append(
                    f"agents/{agent.id}.yaml: action '{name}' references missing prompt file '{act.prompt}'"
                )

    if rules:
        seen: set[str] = set()
        for r in rules.rules:
            if r.id in seen:
                errors.append(f"rules.yaml: duplicate rule id '{r.id}'")
            seen.add(r.id)
            _check_template(jinja, f"rules.yaml: rule '{r.id}' explain", r.explain, errors)

    if doc_types:
        ids = [lbl.id for lbl in doc_types.labels]
        for dup in {i for i in ids if ids.count(i) > 1}:
            errors.append(f"document_types.yaml: duplicate label id '{dup}'")

    if app and erp_profiles and app.app.default_erp_profile not in erp_profiles:
        errors.append(
            f"app.yaml: default_erp_profile '{app.app.default_erp_profile}' has no erp_profiles/ file"
        )

    if workflow:
        errors.extend(validate_workflow(workflow, agents))

    if errors or not (app and workflow and rules and doc_types):
        raise ConfigError(errors or ["config could not be loaded"])

    return ResolvedConfig(
        app=app,
        workflow=workflow,
        rules=rules,
        document_types=doc_types,
        agents=agents,
        erp_profiles=erp_profiles,
        prompts=prompts,
        files=dict(files),
        hash=config_hash(files),
    )


def validate_workflow(wf: Workflow, agents: dict[str, AgentConfig]) -> list[str]:
    errors: list[str] = []
    ids = [s.id for s in wf.stages]
    stage_ids = set(ids)
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"workflow.yaml: duplicate stage id '{dup}'")
    if wf.start not in stage_ids:
        errors.append(f"workflow.yaml: start stage '{wf.start}' does not exist")
    terminal = sorted(TERMINAL_STATUSES)

    def check_target(where: str, target: str) -> None:
        if target.startswith(END_PREFIX):
            status = target[len(END_PREFIX) :]
            if status not in terminal:
                errors.append(
                    f"{where}: unknown terminal status '{target}' (allowed: end:{', end:'.join(terminal)})"
                )
        elif target not in stage_ids:
            errors.append(f"{where}: target '{target}' is not a stage id")

    review_actions = [a.value for a in ReviewAction]
    for st in wf.stages:
        where = f"workflow.yaml: stage '{st.id}'"
        mapped = set(st.next)
        if st.hitl:
            declared, label = review_actions, "review action"
        else:
            agent = agents.get(st.agent or "")
            if agent is None:
                errors.append(f"{where}: unknown agent '{st.agent}'")
                continue
            action = agent.actions.get(st.action or "")
            if action is None:
                errors.append(
                    f"{where}: agent '{agent.id}' has no action '{st.action}' "
                    f"(available: {', '.join(agent.actions)})"
                )
                continue
            declared, label = action.outcomes, f"outcome of {agent.id}.{st.action}"
            if not agent.enabled and SKIPPED not in mapped:
                errors.append(
                    f"{where}: agent '{agent.id}' is disabled, so the stage returns '{SKIPPED}'; "
                    f"map `{SKIPPED}` under `next:`"
                )
        for missing in [o for o in declared if o not in mapped]:
            errors.append(f"{where}: unmapped {label} '{missing}'; add it under `next:`")
        allowed_extra = set() if st.hitl else {SKIPPED}
        for unknown in sorted(mapped - set(declared) - allowed_extra):
            errors.append(f"{where}: `next` maps '{unknown}', which is not a declared {label}")
        for outcome, target in st.next.items():
            check_target(f"{where} next.{outcome}", target)
        if st.resume_to:
            check_target(f"{where} resume_to", st.resume_to)

    all_outcomes = {o for st in wf.stages for o in st.next}
    for outcome, target in wf.options.sticky_review_redirect.items():
        if outcome not in all_outcomes:
            errors.append(
                f"workflow.yaml: options.sticky_review_redirect: '{outcome}' is not an outcome any stage maps"
            )
        check_target(f"workflow.yaml: options.sticky_review_redirect.{outcome}", target)

    # reachability from start (next targets, resume_to, sticky redirects)
    if wf.start in stage_ids:
        by_id = {s.id: s for s in wf.stages}
        seen = {wf.start}
        todo = deque([wf.start])
        while todo:
            st = by_id[todo.popleft()]
            targets = list(st.next.values()) + ([st.resume_to] if st.resume_to else [])
            if wf.options.sticky_review:
                targets += [t for o, t in wf.options.sticky_review_redirect.items() if o in st.next]
            for t in targets:
                if t in by_id and t not in seen:
                    seen.add(t)
                    todo.append(t)
        for sid in ids:
            if sid not in seen:
                errors.append(f"workflow.yaml: stage '{sid}' is unreachable from start '{wf.start}'")
    return errors


def snapshot_json(cfg: ResolvedConfig) -> dict[str, Any]:
    return {"hash": cfg.hash, "files": cfg.files}


def from_snapshot(snapshot: dict[str, Any]) -> ResolvedConfig:
    return build_config(snapshot["files"])
