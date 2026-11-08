"""RibonanzaNet-SS comes up as a fourth method through the same framework.

Only a manifest + a runner + one RUNNERS line were added; its output (an L×L
base-pairing probability matrix) reuses the existing `contact_map` kind, parallel to
structRFM's attention map. These exercise the full lifecycle — form, generic submission,
status, matrix output — against the fake backend.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def ribo_client(tmp_path) -> Iterator[TestClient]:
    settings = Settings(db_path=tmp_path / "r.db", backend="fake")
    with TestClient(create_app(settings)) as c:
        yield c


def _poll(client: TestClient, run_id: str) -> dict:
    body: dict = {}
    for _ in range(5):
        body = client.get(f"/api/runs/{run_id}").json()
        if body["state"] in {"succeeded", "failed", "canceled"}:
            break
    return body


def test_catalog_and_method_page(ribo_client):
    catalog = ribo_client.get("/").text
    assert "RibonanzaNet" in catalog
    page = ribo_client.get("/methods/ribonanzanet").text
    assert "RibonanzaNet" in page
    assert "sequence" in page.lower()
    assert "Submit run" in page


def test_submit_sequence_returns_matrix(ribo_client):
    resp = ribo_client.post(
        "/api/runs", json={"method": "ribonanzanet", "sequence": "GGGGAAAACCCC", "seed": 1}
    )
    assert resp.status_code == 201
    assert resp.json()["method"] == "ribonanzanet"
    final = _poll(ribo_client, resp.json()["id"])
    assert final["state"] == "succeeded"
    assert final["sequence"] == "GGGGAAAACCCC"
    m = final["matrix"]
    assert len(m) == 12 and all(len(row) == 12 for row in m)
    assert m == [list(r) for r in zip(*m, strict=True)]  # symmetric
    assert all(0.0 <= v <= 1.0 for row in m for v in row)
    assert final["matrix_type"] == "base_pairing"  # not structRFM's "attention"
    assert final["metrics"]["length"] == 12


def test_bad_alphabet_is_422(ribo_client):
    resp = ribo_client.post("/api/runs", json={"method": "ribonanzanet", "sequence": "GGGZZZCCC"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_alphabet"


def test_n_base_is_rejected(ribo_client):
    # N passes the generic RNA alphabet, but RibonanzaNet-SS has no N token — the
    # per-method validator must reject it at submit, not let the worker KeyError.
    resp = ribo_client.post("/api/runs", json={"method": "ribonanzanet", "sequence": "GGGGNAACCCC"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "unsupported_base"


def test_use_example_returns_matrix(ribo_client):
    resp = ribo_client.post("/api/runs", json={"method": "ribonanzanet", "use_example": True})
    assert resp.status_code == 201
    final = _poll(ribo_client, resp.json()["id"])
    assert final["state"] == "succeeded"
    assert final["matrix"]


def test_run_page_shows_base_pairing_heatmap(ribo_client):
    run_id = ribo_client.post(
        "/api/runs", json={"method": "ribonanzanet", "sequence": "GGGGAAAACCCC", "seed": 1}
    ).json()["id"]
    ribo_client.get(f"/runs/{run_id}")
    page = ribo_client.get(f"/runs/{run_id}").text
    assert "SUCCEEDED" in page
    assert "base-pairing probability matrix" in page.lower()  # not structRFM's wording
    assert "structRFM" not in page  # no structRFM wording leaks (heading, caption, aria-label)
    assert 'class="contact-heatmap"' in page
    # fake backend runs the stub, so the placeholder banner shows
    assert "Mockup output" in page
