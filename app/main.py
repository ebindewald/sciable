"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from app import __version__, api, web
from app.backends.base import Backend
from app.backends.fake import FakeBackend
from app.bootstrap import ensure_seeded
from app.config import REPO_ROOT, Settings, get_settings
from app.db import init_db
from app.manifest import get_manifest
from app.runs import RunService

# Load a local .env into the process environment (without overriding real env
# vars) so secrets referenced by name — e.g. RUNPOD_API_KEY — are visible to the
# backend. The .env file itself is gitignored and never stored by the app.
load_dotenv(REPO_ROOT / ".env", override=False)


def build_backend(settings: Settings) -> Backend:
    if settings.backend == "fake":
        return FakeBackend()
    if settings.backend == "runpod":
        from app.backends.runpod import RunpodBackend

        return RunpodBackend(settings)
    raise ValueError(f"Unknown backend: {settings.backend!r}")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    manifest = get_manifest(settings.method)
    backend = build_backend(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(settings.db_path)
        deployment_id = ensure_seeded(settings, manifest)
        app.state.deployment_id = deployment_id
        yield

    app = FastAPI(title="Sciable", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.manifest = manifest
    app.state.backend = backend
    app.state.run_service = RunService(settings, manifest, backend)
    app.include_router(web.router)
    app.include_router(api.router)
    return app


app = create_app()
