"""Scenario launcher: list the synthetic fixtures in data/scenarios.yaml and run one as a new case.

The fixture PDFs are shared baseline files (app.baseline): a run's document points at the one stored copy.
A scenario that builds on another (S02b on S02a, S04 on S01) runs that one first when the workspace has no
run of it yet; the response lists those prerequisite cases.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.cases import case_out
from app.api.deps import current_config, get_baseline, get_orchestrator, get_session, get_storage
from app.api.schemas import FixtureOut, ScenarioRunOut
from app.baseline import Baseline, BaselineError
from app.config_loader import ResolvedConfig
from app.orchestrator import Orchestrator
from app.scenarios import ScenarioSpec
from app.storage import Storage

router = APIRouter(prefix="/api/fixtures", tags=["fixtures"])

BaselineDep = Annotated[Baseline, Depends(get_baseline)]


def _scenario(baseline: Baseline, scenario_id: str) -> ScenarioSpec:
    try:
        return baseline.scenarios.scenario(scenario_id)
    except KeyError:
        raise HTTPException(404, f"unknown scenario {scenario_id}") from None


@router.get("", response_model=list[FixtureOut])
def list_fixtures(baseline: BaselineDep) -> list[dict[str, Any]]:
    sc = baseline.scenarios
    return [
        {
            "id": s.id,
            "title": s.title,
            "file": s.file,
            "render": s.render,
            "run_order": sc.run_order.index(s.id) + 1 if s.id in sc.run_order else None,
            "expect": s.expect,
            "demo_remark": s.demo_remark,
            "requires": s.requires,
            "available": baseline.available(s.file),
            "supporting_files": [
                {"file": f.file, "kind": f.kind, "available": baseline.available(f.file)} for f in s.supporting_files
            ],
        }
        for s in sc.scenarios
    ]


@router.post("/{scenario_id}/run", response_model=ScenarioRunOut, status_code=201)
async def run_fixture(
    scenario_id: str,
    baseline: BaselineDep,
    s: Annotated[Session, Depends(get_session)],
    orch: Annotated[Orchestrator, Depends(get_orchestrator)],
    storage: Annotated[Storage, Depends(get_storage)],
    cfg: Annotated[ResolvedConfig, Depends(current_config)],
) -> dict[str, Any]:
    """Create a case from the scenario's fixture file (source=fixture) and queue it, after the cases of any
    prerequisite scenario it had to run first (each one starts when the previous run ends)."""
    scenario = _scenario(baseline, scenario_id)

    def start() -> dict[str, Any]:  # publishing a baseline file renders it: off the event loop
        try:
            cases = baseline.scenario_cases(s, cfg, storage, scenario)
        except BaselineError as e:
            raise HTTPException(e.status, str(e)) from e
        for prev, nxt in pairwise(cases):
            orch.enqueue_after(nxt.id, prev.id)
        orch.enqueue(cases[0].id)
        out = case_out(s, orch, cases[-1])
        out["prerequisites"] = [{"id": c.id, "ref": c.ref, "scenario_id": c.scenario_id} for c in cases[:-1]]
        return out

    return await run_in_threadpool(start)


@router.get("/{scenario_id}/files/{filename}")
def fixture_file(scenario_id: str, filename: str, baseline: BaselineDep) -> Response:
    """Download a scenario's PDF or one of its supporting files (e.g. the S05 W-9 for the vendor portal),
    from the shared read-only baseline copy."""
    scenario = _scenario(baseline, scenario_id)
    if filename not in {scenario.file, *(f.file for f in scenario.supporting_files)}:
        raise HTTPException(404, f"{filename} is not a file of {scenario_id}")
    try:
        data = baseline.read(filename)
    except BaselineError as e:
        raise HTTPException(e.status, str(e)) from e
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
