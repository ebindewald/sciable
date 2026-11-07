"""Shared test fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.backends.fake import FakeBackend
from app.bootstrap import ensure_seeded
from app.config import Settings, get_settings
from app.db import init_db
from app.main import create_app
from app.manifest import get_manifest
from app.runs import RunService


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch):
    # Tests must not inherit the developer's local .env (which pins method/backend
    # for the running server). app.main calls load_dotenv() at import, so those
    # values land in os.environ where pydantic reads them; disabling env_file is not
    # enough. Strip the leaked vars and drop the cached global settings/manifest so
    # the suite depends only on explicit kwargs and code defaults.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for key in [k for k in os.environ if k.startswith("SCIABLE_") or k == "RUNPOD_API_KEY"]:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    get_manifest.cache_clear()
    yield
    get_settings.cache_clear()
    get_manifest.cache_clear()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Isolated DB per test; default FakeBackend (no GPU, no credentials).
    return Settings(db_path=tmp_path / "test.db", backend="fake")


@pytest.fixture
def manifest():
    return get_manifest("grnade")


@pytest.fixture
def example_pdb(manifest) -> str:
    return manifest.example_path().read_text()


@pytest.fixture
def run_service(settings: Settings, manifest):
    """A RunService wired to a seeded DB and a fresh FakeBackend."""
    init_db(settings.db_path)
    deployment_id = ensure_seeded(settings, manifest)
    service = RunService(settings, manifest, FakeBackend())
    return service, deployment_id


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as c:
        yield c
