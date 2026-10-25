from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import StubScript
from app.cases import UploadedFile, create_case
from app.config_loader import ResolvedConfig, build_config, read_config_files
from app.db import init_db, make_engine, make_session_factory
from app.llm import FakeLLM
from app.models import Case, User
from app.orchestrator import Orchestrator
from app.seed import load_master
from app.settings import BACKEND_DIR, REPO_ROOT, Settings
from app.storage import LocalStorage, OwnerStorage
from app.tenancy import bind_owner

CONFIG_DIR = REPO_ROOT / "config"
FIXTURE_DIR = REPO_ROOT / "data" / "fixtures"
LLM_FIXTURES = BACKEND_DIR / "tests" / "llm_fixtures"
MASTER_DIR = REPO_ROOT / "data" / "master"

# ---------------------------------------------------------------- auth for tests
# API tests sign HS256 tokens with the "legacy JWT secret" configured in test_settings; the JWKS path
# (ES256/RS256) is covered in test_auth_gate.py.
SUPABASE_URL = "https://test-project.supabase.co"
JWT_SECRET = "test-jwt-secret-for-hs256-signing-only-0123456789"
ISSUER = f"{SUPABASE_URL}/auth/v1"


@dataclass(frozen=True)
class TestUser:
    id: str
    email: str
    name: str


ALICE = TestUser("11111111-1111-4111-8111-111111111111", "alice@example.com", "Alice Example")
BOB = TestUser("22222222-2222-4222-8222-222222222222", "bob@example.com", "Bob Example")


def make_token(user: TestUser, *, secret: str = JWT_SECRET, ttl: int = 3600, **claims: Any) -> str:
    now = int(time.time())
    payload = {"sub": user.id, "email": user.email, "aud": "authenticated", "role": "authenticated",
               "iss": ISSUER, "iat": now, "exp": now + ttl, "user_metadata": {"full_name": user.name}}
    payload.update(claims)
    return jwt.encode(payload, secret, algorithm="HS256")


def auth_headers(user: TestUser = ALICE) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user)}"}


def auth_client(app: FastAPI, user: TestUser = ALICE, **kw: Any) -> TestClient:
    """A TestClient signed in as `user` (use it as a context manager to run the app's lifespan)."""
    return TestClient(app, headers=auth_headers(user), **kw)


# ---------------------------------------------------------------- config
@pytest.fixture
def config_files() -> dict[str, str]:
    return read_config_files(CONFIG_DIR)


def patch_yaml(files: dict[str, str], path: str, mutate: Callable[[dict], None]) -> dict[str, str]:
    """Return a copy of `files` with one YAML file changed by `mutate`."""
    data = yaml.safe_load(files[path])
    mutate(data)
    return {**files, path: yaml.safe_dump(data, sort_keys=False)}


# ---------------------------------------------------------------- database
def add_user(sf: sessionmaker[Session], user: TestUser = ALICE) -> None:
    with sf() as s:
        s.add(User(id=user.id, email=user.email, name=user.name))
        s.commit()


@pytest.fixture
def engine_factory(tmp_path: Path) -> sessionmaker[Session]:
    """Unbound session factory over a migrated, empty database (system code: users, cross-owner checks)."""
    engine = make_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    init_db(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def session_factory(engine_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    """Sessions bound to ALICE's workspace (her user row exists; her workspace is empty)."""
    add_user(engine_factory, ALICE)
    return bind_owner(engine_factory, ALICE.id)


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}",
        storage_dir=tmp_path / "storage",
        config_dir=CONFIG_DIR,
        force_stub_agents=True,
        azure_openai_endpoint="",
        auth_mode="supabase",
        supabase_url=SUPABASE_URL,
        supabase_jwt_secret=JWT_SECRET,
    )


def api_sessions(settings: Settings, user: TestUser = ALICE) -> tuple[Any, sessionmaker[Session]]:
    """(engine, session factory bound to `user`) over an API test's database, for direct checks."""
    engine = make_engine(settings.database_url)
    return engine, bind_owner(make_session_factory(engine), user.id)


def owner_storage(storage: LocalStorage, user: TestUser = ALICE) -> OwnerStorage:
    return OwnerStorage(storage, user.id)


# ---------------------------------------------------------------- harnesses
class Harness:
    def __init__(self, sf: sessionmaker[Session], storage: LocalStorage):
        self.sf = sf
        self.storage = owner_storage(storage)
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
    """Real agents where a handler exists, StubAgent for the rest, FakeLLM for every model call. Works in
    ALICE's workspace: `storage` is her view of the store (keys under u/<alice>/)."""

    def __init__(self, sf: sessionmaker[Session], storage: LocalStorage, llm: Any):
        self.sf = sf
        self.root_storage = storage
        self.storage = owner_storage(storage)
        self.llm = llm
        self.orch: Orchestrator | None = None

    async def start(self, files: dict[str, str] | None = None, *, master: bool = True) -> Orchestrator:
        """`master`: load data/master/*.csv (vendors, POs, GRNs, GL accounts) first."""
        self.cfg = build_config(files or read_config_files(CONFIG_DIR))
        if master:
            with self.sf() as s:
                load_master(s, MASTER_DIR)
                s.commit()
        cfg = self.cfg
        self.orch = Orchestrator(self.sf, lambda: cfg, llm=self.llm, storage=self.root_storage)
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
