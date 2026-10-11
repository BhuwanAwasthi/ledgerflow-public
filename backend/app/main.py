"""FastAPI app: CORS, routers, DB init, config validation and orchestrator startup resume."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.base import StubScript
from app.api import cases, fixtures, system
from app.config_loader import resolved_config
from app.db import init_db, make_engine, make_session_factory
from app.orchestrator import Orchestrator
from app.settings import Settings, get_settings
from app.storage import make_storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, *, script: StubScript | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        init_db(engine)
        session_factory = make_session_factory(engine)

        def config_provider():  # files + active DB overrides; raises ConfigError if invalid
            with session_factory() as s:
                return resolved_config(settings.config_dir, s)

        cfg = config_provider()  # fail fast on an invalid config
        app.state.settings = settings
        app.state.session_factory = session_factory
        app.state.storage = make_storage(settings)
        app.state.config_hash = cfg.hash
        app.state.llm_path = "not_probed" if settings.azure_openai_endpoint else "unconfigured"
        orch = Orchestrator(
            session_factory, config_provider, script=script, force_stub=settings.force_stub_agents
        )
        app.state.orchestrator = orch
        await orch.start()
        resumed = await orch.resume_pending()
        logging.getLogger(__name__).info("startup: config %s, resumed %d case(s)", cfg.hash[:12], resumed)
        try:
            yield
        finally:
            await orch.stop()
            engine.dispose()

    app = FastAPI(title="LedgerFlow API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(system.router)
    app.include_router(cases.router)
    app.include_router(fixtures.router)
    return app


app = create_app()
