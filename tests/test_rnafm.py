"""RNA-FM comes up as a method through the same framework.

Only a manifest + a runner + the new ``embedding`` output kind were added. These
exercise the full lifecycle — form, generic submission, status, embedding output —
against the fake backend.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

EXAMPLE = "GGCACUUCGGUGCC"


@pytest.fixture
def rnafm_client(tmp_path) -> Iterator[TestClient]:
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


def test_catalog_and_method_page(rnafm_client):
    catalog = rnafm_client.get("/").text
    assert "RNA-FM" in catalog and "gRNAde" in catalog  # both reachable
    page = rnafm_client.get("/methods/rnafm").text
    assert "RNA-FM" in page
    assert "sequence" in page.lower()
    assert "Submit run" in page


def test_submit_sequence_returns_embedding(rnafm_client):
    resp = rnafm_client.post(
        "/api/runs", json={"method": "rnafm", "sequence": EXAMPLE, "seed": 1}
    )
    assert resp.status_code == 201
    assert resp.json()["method"] == "rnafm"
    final = _poll(rnafm_client, resp.json()["id"])
    assert final["state"] == "succeeded"
    assert final["sequence"] == EXAMPLE
    emb = final["embedding"]
    assert len(emb) == len(EXAMPLE)
    assert all(len(row) == 640 for row in emb)
    assert final["embedding_dim"] == 640
    assert len(final["pooled"]) == 640
    assert final["metrics"]["length"] == len(EXAMPLE)
    assert final["metrics"]["dim"] == 640
    # Stub depends only on position, so two different equal-length sequences match.
    other = _poll(
        rnafm_client,
        rnafm_client.post(
            "/api/runs", json={"method": "rnafm", "sequence": "AAAAAAAAAAAAAA"}
        ).json()["id"],
    )
    assert other["embedding"] == emb  # positional-only stub => identical


def test_bad_alphabet_is_422(rnafm_client):
    resp = rnafm_client.post("/api/runs", json={"method": "rnafm", "sequence": "GGGZZZCCC"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_alphabet"


def test_over_length_is_422(rnafm_client):
    resp = rnafm_client.post(
        "/api/runs", json={"method": "rnafm", "sequence": "A" * 501}
    )
    assert resp.status_code == 422


def test_use_example_returns_embedding(rnafm_client):
    resp = rnafm_client.post("/api/runs", json={"method": "rnafm", "use_example": True})
    assert resp.status_code == 201
    final = _poll(rnafm_client, resp.json()["id"])
    assert final["state"] == "succeeded"
    assert final["embedding"]
    assert final["sequence"] == EXAMPLE


def test_run_page_shows_heatmap_and_banner(rnafm_client):
    run_id = rnafm_client.post(
        "/api/runs", json={"method": "rnafm", "sequence": EXAMPLE, "seed": 1}
    ).json()["id"]
    rnafm_client.get(f"/runs/{run_id}")
    page = rnafm_client.get(f"/runs/{run_id}").text
    assert "SUCCEEDED" in page
    assert "embedding" in page.lower()
    assert 'class="embedding-heatmap"' in page       # the heatmap SVG is rendered
    # fake backend output is the stub, so the placeholder banner shows
    assert "Mockup output" in page
