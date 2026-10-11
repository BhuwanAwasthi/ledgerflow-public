"""Scenario launcher: list the synthetic fixtures in data/scenarios.yaml and run one as a new case."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.cases import case_out
from app.api.deps import current_config, get_orchestrator, get_session, get_settings_dep, get_storage
from app.api.schemas import CaseOut, FixtureOut
from app.cases import UploadedFile, create_case
from app.config_loader import ResolvedConfig
from app.orchestrator import Orchestrator
from app.scenarios import Scenarios, ScenarioSpec, load_scenarios
from app.settings import Settings
from app.storage import Storage

router = APIRouter(prefix="/api/fixtures", tags=["fixtures"])

SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def _scenarios(settings: Settings) -> Scenarios:
    return load_scenarios(settings.data_dir / "scenarios.yaml")


def _fixture_dir(settings: Settings) -> Path:
    return settings.data_dir / "fixtures"


def _scenario(sc: Scenarios, scenario_id: str) -> ScenarioSpec:
    try:
        return sc.scenario(scenario_id)
    except KeyError:
        raise HTTPException(404, f"unknown scenario {scenario_id}") from None


def _submitter(sc: Scenarios, scenario: ScenarioSpec) -> tuple[str, str]:
    """The vendor's billing contact when the file is from one vendor, else the buyer's AP mailbox."""
    invoices = sc.invoices(scenario)
    if len(invoices) == 1:
        inv = invoices[0]
        if inv.vendor:
            v = sc.vendor(inv.vendor)
            return v.legal_name, f"billing@{v.email_domain}"
        if scenario.new_vendor:
            return scenario.new_vendor.legal_name, f"billing@{scenario.new_vendor.email_domain}"
    return f"{sc.buyer.name} mailroom", sc.buyer.ap_email


@router.get("", response_model=list[FixtureOut])
def list_fixtures(settings: SettingsDep) -> list[dict[str, Any]]:
    sc = _scenarios(settings)
    fx = _fixture_dir(settings)
    return [
        {
            "id": s.id,
            "title": s.title,
            "file": s.file,
            "render": s.render,
            "run_order": sc.run_order.index(s.id) + 1 if s.id in sc.run_order else None,
            "expect": s.expect,
            "demo_remark": s.demo_remark,
            "available": (fx / s.file).is_file(),
            "supporting_files": [
                {"file": f.file, "kind": f.kind, "available": (fx / f.file).is_file()} for f in s.supporting_files
            ],
        }
        for s in sc.scenarios
    ]


@router.post("/{scenario_id}/run", response_model=CaseOut, status_code=201)
def run_fixture(
    scenario_id: str,
    settings: SettingsDep,
    s: Annotated[Session, Depends(get_session)],
    orch: Annotated[Orchestrator, Depends(get_orchestrator)],
    storage: Annotated[Storage, Depends(get_storage)],
    cfg: Annotated[ResolvedConfig, Depends(current_config)],
) -> dict[str, Any]:
    """Create a case from the scenario's fixture file (source=fixture) and queue it."""
    sc = _scenarios(settings)
    scenario = _scenario(sc, scenario_id)
    path = _fixture_dir(settings) / scenario.file
    if not path.is_file():
        raise HTTPException(409, f"{scenario.file} has not been generated: run `make fixtures`")
    name, email = _submitter(sc, scenario)
    case = create_case(
        s,
        cfg,
        storage,
        [UploadedFile(scenario.file, "application/pdf", path.read_bytes())],
        submitter_name=name,
        submitter_email=email,
        note=f"Scenario {scenario.id}: {scenario.title}",
        source="fixture",
    )
    orch.enqueue(case.id)
    return case_out(s, orch, case)


@router.get("/{scenario_id}/files/{filename}", response_class=FileResponse)
def fixture_file(scenario_id: str, filename: str, settings: SettingsDep) -> FileResponse:
    """Download a scenario's PDF or one of its supporting files (e.g. the S05 W-9 for the vendor portal)."""
    scenario = _scenario(_scenarios(settings), scenario_id)
    allowed = {scenario.file, *(f.file for f in scenario.supporting_files)}
    path = _fixture_dir(settings) / filename
    if filename not in allowed or not path.is_file():
        raise HTTPException(404, f"{filename} is not a file of {scenario_id}")
    return FileResponse(path, media_type="application/pdf", filename=filename)
