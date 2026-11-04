"""structRFM comes up as a second method through the same framework.

Only a manifest + a runner were added; selecting it is a config flip
(SCIABLE_METHOD=structrfm). These exercise the full lifecycle — form, generic
submission, status, structure output — against the fake backend.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def structrfm_client(tmp_path) -> Iterator[TestClient]:
    settings = Settings(db_path=tmp_path / "s.db", backend="fake", method="structrfm")
    with TestClient(create_app(settings)) as c:
        yield c


def _poll(client: TestClient, run_id: str) -> dict:
    body: dict = {}
    for _ in range(5):
        body = client.get(f"/api/runs/{run_id}").json()
        if body["state"] in {"succeeded", "failed", "canceled"}:
            break
    return body


def test_index_shows_structrfm(structrfm_client):
    text = structrfm_client.get("/").text
    assert "structRFM" in text
    assert "sequence" in text.lower()
    assert "Submit run" in text


def test_submit_sequence_predicts_structure(structrfm_client):
    resp = structrfm_client.post("/api/runs", json={"sequence": "GGGGAAAACCCC", "seed": 1})
    assert resp.status_code == 201
    assert resp.json()["method"] == "structrfm"
    final = _poll(structrfm_client, resp.json()["id"])
    assert final["state"] == "succeeded"
    assert final["sequence"] == "GGGGAAAACCCC"
    assert set(final["structure"]) <= set("().")
    assert len(final["structure"]) == 12
    assert final["metrics"]["length"] == 12


def test_bad_alphabet_is_422(structrfm_client):
    resp = structrfm_client.post("/api/runs", json={"sequence": "GGGZZZCCC"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_alphabet"


def test_use_example_predicts_structure(structrfm_client):
    resp = structrfm_client.post("/api/runs", json={"use_example": True})
    assert resp.status_code == 201
    final = _poll(structrfm_client, resp.json()["id"])
    assert final["state"] == "succeeded"
    assert final["structure"]


def test_run_page_shows_structure(structrfm_client):
    run_id = structrfm_client.post(
        "/api/runs", json={"sequence": "GGGGAAAACCCC", "seed": 1}
    ).json()["id"]
    structrfm_client.get(f"/runs/{run_id}")
    page = structrfm_client.get(f"/runs/{run_id}").text
    assert "SUCCEEDED" in page
    assert "secondary structure" in page.lower()
