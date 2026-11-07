"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from app import __version__, api, web
from app.backends.base import Backend
from app.backends.fake import FakeBackend
from app.bootstrap import ensure_all_seeded
from app.config import REPO_ROOT, Settings, get_settings
from app.db import init_db
from app.manifest import discover_manifests
from app.runs import RunService

# Load a local .env into the process environment (without overriding real env
# vars) so secrets referenced by name — e.g. RUNPOD_API_KEY — are visible to the
# backend. The .env file itself is gitignored and never stored by the app.
load_dotenv(REPO_ROOT / ".env", override=False)


def build_backend(settings: Settings) -> Backend:
    if settings.backend == "fake":
        # LOUD on purpose: the fake backend returns deterministic STUB output, not
        # real inference. Mistaking it for real has cost real debugging time, so make
        # it impossible to miss which backend is live (set SCIABLE_BACKEND=runpod for
        # real runs).
        import sys

        banner = (
            "=" * 72 + "\n"
            "  ⚠  SCIABLE_BACKEND=fake — ALL run output is STUB/placeholder, NOT real\n"
            "     inference. Set SCIABLE_BACKEND=runpod (and the endpoint ids) for real\n"
            "     results. This backend exists only for GPU-free local/CI testing.\n"
            + "=" * 72
        )
        print(banner, file=sys.stderr, flush=True)
        return FakeBackend()
    if settings.backend == "runpod":
        from app.backends.runpod import RunpodBackend

        return RunpodBackend(settings)
    raise ValueError(f"Unknown backend: {settings.backend!r}")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    manifests = discover_manifests()
    backend = build_backend(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(settings.db_path)
        app.state.deployments = ensure_all_seeded(settings, manifests)
        yield

    app = FastAPI(title="Sciable", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.manifests = manifests
    app.state.backend = backend
    app.state.run_service = RunService(settings, manifests, backend)
    app.include_router(web.router)
    app.include_router(api.router)
    return app


app = create_app()
