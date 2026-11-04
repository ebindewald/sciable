"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.backends.fake import FakeBackend
from app.bootstrap import ensure_seeded
from app.config import Settings
from app.db import init_db
from app.main import create_app
from app.manifest import get_manifest
from app.runs import RunService


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Isolated DB per test; default FakeBackend (no GPU, no credentials).
    return Settings(db_path=tmp_path / "test.db", backend="fake")


@pytest.fixture
def manifest():
    return get_manifest()


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
