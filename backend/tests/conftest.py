from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
import yaml
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import StubScript
from app.cases import UploadedFile, create_case
from app.config_loader import ResolvedConfig, build_config, read_config_files
from app.db import init_db, make_engine, make_session_factory
from app.models import Case
from app.orchestrator import Orchestrator
from app.settings import REPO_ROOT, Settings
from app.storage import LocalStorage

CONFIG_DIR = REPO_ROOT / "config"


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
