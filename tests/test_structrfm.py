"""structRFM comes up as a second method through the same framework.

Only a manifest + a runner were added; selecting it is a config flip
(SCIABLE_METHOD=structrfm). These exercise the full lifecycle — form, generic
submission, status, contact-map (attention) output — against the fake backend.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def structrfm_client(tmp_path) -> Iterator[TestClient]:
    settings = Settings(db_path=tmp_path / "s.db", backend="fake")
    with TestClient(create_app(settings)) as c:
        yield c


def _poll(client: TestClient, run_id: str) -> dict:
    body: dict = {}
    for _ in range(5):
        body = client.get(f"/api/runs/{run_id}").json()
        if body["state"] in {"succeeded", "failed", "canceled"}:
            break
    return body


def test_catalog_and_method_page(structrfm_client):
    catalog = structrfm_client.get("/").text
    assert "structRFM" in catalog and "gRNAde" in catalog  # both reachable
    page = structrfm_client.get("/methods/structrfm").text
    assert "structRFM" in page
    assert "sequence" in page.lower()
    assert "Submit run" in page


def test_submit_sequence_returns_matrix(structrfm_client):
    resp = structrfm_client.post(
        "/api/runs", json={"method": "structrfm", "sequence": "GGGGAAAACCCC", "seed": 1}
    )
    assert resp.status_code == 201
    assert resp.json()["method"] == "structrfm"
    final = _poll(structrfm_client, resp.json()["id"])
    assert final["state"] == "succeeded"
    assert final["sequence"] == "GGGGAAAACCCC"
    m = final["matrix"]
    assert len(m) == 12 and all(len(row) == 12 for row in m)
    assert m == [list(r) for r in zip(*m, strict=True)]  # symmetric
    assert final["matrix_type"] == "attention"
    assert final["metrics"]["length"] == 12


def test_bad_alphabet_is_422(structrfm_client):
    resp = structrfm_client.post("/api/runs", json={"method": "structrfm", "sequence": "GGGZZZCCC"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_alphabet"


def test_use_example_returns_matrix(structrfm_client):
    resp = structrfm_client.post("/api/runs", json={"method": "structrfm", "use_example": True})
    assert resp.status_code == 201
    final = _poll(structrfm_client, resp.json()["id"])
    assert final["state"] == "succeeded"
    assert final["matrix"]


def test_run_page_shows_heatmap(structrfm_client):
    run_id = structrfm_client.post(
        "/api/runs", json={"method": "structrfm", "sequence": "GGGGAAAACCCC", "seed": 1}
    ).json()["id"]
    structrfm_client.get(f"/runs/{run_id}")
    page = structrfm_client.get(f"/runs/{run_id}").text
    assert "SUCCEEDED" in page
    assert "attention map" in page.lower()
    assert 'class="contact-heatmap"' in page       # the heatmap SVG is rendered
    # fake backend output has no real engine, so the placeholder banner shows
    assert "Mockup output" in page


def test_dot_bracket_pairs_resolves_nesting():
    # The dot-bracket helpers stay (generic, for a future dot-bracket method).
    from app.api import dot_bracket_pairs

    assert dot_bracket_pairs("((((....))))") == [(0, 11), (1, 10), (2, 9), (3, 8)]
    assert dot_bracket_pairs("((.)") == [(1, 3)]
    assert dot_bracket_pairs("....") == []
