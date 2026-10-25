"""FastAPI app: the auth gate, CORS, routers, migrations, config validation and orchestrator startup resume.

Default-deny: `auth_gate` is an app-level dependency, so every route needs a signed-in user unless its path
is in app.auth.PUBLIC_PATHS (only /healthz; plus the stub login when AUTH_MODE=dev).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from concurrent.futures import Future
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import log_redaction
from app.agents.base import StubScript
from app.api import cases, dashboard, exports, fixtures, me, portal, studio, system, vendors
from app.auth import DEV_LOGIN_PATH, PUBLIC_PATHS, JWTVerifier, auth_gate, check_dev_mode
from app.baseline import Baseline
from app.config_loader import resolved_config
from app.db import init_db, make_engine, make_session_factory
from app.llm import AzureLLM, make_llm
from app.orchestrator import Orchestrator
from app.settings import Settings, get_settings
from app.storage import make_storage
from app.tokens import FileTokens

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(
    settings: Settings | None = None, *, script: StubScript | None = None, verifier: JWTVerifier | None = None
) -> FastAPI:
    settings = settings or get_settings()
    check_dev_mode(settings)  # AUTH_MODE=dev never starts near production
    dev = settings.auth_mode == "dev"
    verifier = verifier or JWTVerifier.from_settings(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log_redaction.install()
        if not verifier.configured:
            raise RuntimeError(
                "AUTH_MODE=supabase needs SUPABASE_URL (JWKS) or SUPABASE_JWT_SECRET. "
                "For local development without Supabase, set AUTH_MODE=dev."
            )
        engine = make_engine(settings.database_url)
        init_db(engine)
        session_factory = make_session_factory(engine)

        def config_provider():  # the config files: baseline for system work (per-user edits live in cases)
            return resolved_config(settings.config_dir)

        cfg = config_provider()  # fail fast on an invalid config
        probe: Future[str] | None = None
        storage = make_storage(settings)
        app.state.settings = settings
        app.state.session_factory = session_factory
        app.state.base_config = cfg
        app.state.config_hash = cfg.hash
        app.state.storage = storage
        app.state.baseline = Baseline(storage, settings.data_dir, cfg)
        app.state.file_tokens = FileTokens(settings.file_token_secret or None)
        app.state.llm = make_llm(settings, cfg, session_factory)
        orch = Orchestrator(
            session_factory,
            config_provider,
            script=script,
            force_stub=settings.force_stub_agents,
            llm=app.state.llm,
            storage=storage,
        )
        app.state.orchestrator = orch
        await orch.start()
        if isinstance(app.state.llm, AzureLLM) and app.state.llm.path == "not_probed":
            # pick responses|chat once, in the background, on the loop that makes every LLM call
            probe = orch.submit(app.state.llm.probe())
        resumed = await orch.resume_pending()
        logging.getLogger(__name__).info(
            "startup: config %s, auth %s, resumed %d case(s)", cfg.hash[:12], settings.auth_mode, resumed
        )
        try:
            yield
        finally:
            if probe is not None:
                probe.cancel()
            await orch.stop()
            engine.dispose()

    app = FastAPI(
        title="LedgerFlow API",
        version="0.1.0",
        lifespan=lifespan,
        dependencies=[Depends(auth_gate)],
        # the interactive docs and the schema are served in dev only (scripts/dump_openapi builds the schema
        # offline for the frontend types)
        docs_url="/docs" if dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.state.verifier = verifier
    app.state.public_paths = PUBLIC_PATHS | ({DEV_LOGIN_PATH} if dev else set())
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(system.router)
    app.include_router(me.router)
    if dev:
        app.include_router(me.dev_router)
    app.include_router(studio.router)
    app.include_router(cases.router)
    app.include_router(fixtures.router)
    app.include_router(exports.router)
    app.include_router(vendors.router)
    app.include_router(dashboard.router)
    app.include_router(portal.router)
    return app


app = create_app()
