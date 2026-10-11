from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import StubScript
from app.cases import UploadedFile, create_case
from app.config_loader import ResolvedConfig, build_config, read_config_files
from app.db import init_db, make_engine, make_session_factory
from app.llm import FakeLLM
from app.models import Case
from app.orchestrator import Orchestrator
from app.settings import BACKEND_DIR, REPO_ROOT, Settings
from app.storage import LocalStorage

CONFIG_DIR = REPO_ROOT / "config"
FIXTURE_DIR = REPO_ROOT / "data" / "fixtures"
LLM_FIXTURES = BACKEND_DIR / "tests" / "llm_fixtures"


@pytest.fixture
def config_files() -> dict[str, str]:
    return read_config_files(CONFIG_DIR)


def patch_yaml(files: dict[str, str], path: str, mutate: Callable[[dict], None]) -> dict[str, str]:
    """Return a copy of `files` with one YAML file changed by `mutate`."""
    data = yaml.safe_load(files[path])
    mutate(data)
    return {**files, path: yaml.safe_dump(data, sort_keys=False)}


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = make_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    init_db(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}",
        storage_dir=tmp_path / "storage",
        config_dir=CONFIG_DIR,
        force_stub_agents=True,
        azure_openai_endpoint="",
    )


class Harness:
    def __init__(self, sf: sessionmaker[Session], storage: LocalStorage):
        self.sf = sf
        self.storage = storage
        self.orch: Orchestrator | None = None

    async def start(self, outcomes: dict[str, list[str]] | None = None, files: dict[str, str] | None = None):
        cfg = build_config(files or read_config_files(CONFIG_DIR))
        self.cfg: ResolvedConfig = cfg
        self.orch = Orchestrator(self.sf, lambda: cfg, script=StubScript(outcomes), force_stub=True)
        await self.orch.start()
        return self.orch

    def new_case(self) -> int:
        with self.sf() as s:
            case = create_case(
                s,
                self.cfg,
                self.storage,
                [UploadedFile("inv.pdf", "application/pdf", b"%PDF-1.4 test")],
                submitter_name="Test",
                submitter_email="t@example.com",
            )
            return case.id

    def case(self, case_id: int) -> Case:
        with self.sf() as s:
            return s.get(Case, case_id)


@pytest.fixture
async def harness(session_factory: sessionmaker[Session], tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(session_factory, LocalStorage(tmp_path / "storage"))
    yield h
    if h.orch:
        await h.orch.stop()


class Pipeline:
    """Real agents where a handler exists (intake, classifier, extraction, normaliser), StubAgent for the
    rest, FakeLLM for every model call."""

    def __init__(self, sf: sessionmaker[Session], storage: LocalStorage, llm: Any):
        self.sf = sf
        self.storage = storage
        self.llm = llm
        self.orch: Orchestrator | None = None

    async def start(self, files: dict[str, str] | None = None) -> Orchestrator:
        self.cfg = build_config(files or read_config_files(CONFIG_DIR))
        cfg = self.cfg
        self.orch = Orchestrator(self.sf, lambda: cfg, llm=self.llm, storage=self.storage)
        await self.orch.start()
        return self.orch

    async def run(self, *uploads: tuple[str, bytes, str | None], email: str | None = None) -> int:
        assert self.orch is not None
        with self.sf() as s:
            case = create_case(
                s,
                self.cfg,
                self.storage,
                [UploadedFile(name, mime, data) for name, data, mime in uploads],
                submitter_name="Test",
                submitter_email=email,
                source="fixture",
            )
            case_id = case.id
        self.orch.enqueue(case_id)
        await self.orch.join()
        return case_id

    async def run_fixture(self, filename: str, **kw: Any) -> int:
        return await self.run((filename, (FIXTURE_DIR / filename).read_bytes(), "application/pdf"), **kw)


@pytest.fixture
async def pipeline(session_factory: sessionmaker[Session], tmp_path: Path) -> AsyncIterator[Pipeline]:
    p = Pipeline(session_factory, LocalStorage(tmp_path / "storage"), FakeLLM(LLM_FIXTURES))
    yield p
    if p.orch:
        await p.orch.stop()
