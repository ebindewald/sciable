"""API: JSON + multipart submission, polling, validation, conflict, auth (gRNAde)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _poll_to_terminal(client: TestClient, run_id: str, token: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = None
    for _ in range(5):
        body = client.get(f"/api/runs/{run_id}", headers=headers).json()
        if body["state"] in {"succeeded", "failed", "canceled"}:
            break
    return body


def test_submit_json_then_poll_success(client, example_pdb):
    resp = client.post("/api/runs", json={"structure": example_pdb, "seed": 1})
    assert resp.status_code == 201
    assert resp.headers["Location"].startswith("/api/runs/")
    run_id = resp.json()["id"]
    assert resp.json()["state"] == "queued"
    assert resp.json()["method"] == "grnade"

    final = _poll_to_terminal(client, run_id)
    assert final["state"] == "succeeded"
    assert len(final["sequences"]) == 5
    assert all(set(s) <= set("ACGU") for s in final["sequences"])
    assert len(final["metrics"]) == 5
    assert set(final["metrics"][0]) == {"id", "length", "gc_percent"}
    assert final["raw"]["backend"] == "fake"


def test_raw_output_endpoint(client, example_pdb):
    run_id = client.post("/api/runs", json={"structure": example_pdb, "seed": 1}).json()["id"]
    _poll_to_terminal(client, run_id)
    resp = client.get(f"/api/runs/{run_id}/raw")
    assert resp.status_code == 200
    assert resp.json()["backend"] == "fake"
    assert "stdout" in resp.json()


def test_raw_output_404_before_success(client, example_pdb):
    run_id = client.post("/api/runs", json={"structure": example_pdb, "seed": 1}).json()["id"]
    assert client.get(f"/api/runs/{run_id}/raw").status_code == 404


def test_submit_multipart(client, example_pdb):
    resp = client.post(
        "/api/runs",
        files={"structure_file": ("example.pdb", example_pdb, "text/plain")},
        data={"design_count": "3", "seed": "2"},
    )
    assert resp.status_code == 201
    assert resp.json()["inputs"]["design_count"] == 3


def test_submit_use_example_flag(client):
    resp = client.post("/api/runs", json={"use_example": True, "seed": 3})
    assert resp.status_code == 201


def test_missing_structure_is_422(client):
    resp = client.post("/api/runs", json={"seed": 1})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "empty_input"


def test_validation_error_is_422(client, example_pdb):
    resp = client.post("/api/runs", json={"structure": example_pdb, "design_count": 99})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "design_count_out_of_range"


def test_in_flight_conflict_is_409(client, example_pdb):
    assert client.post("/api/runs", json={"structure": example_pdb, "seed": 1}).status_code == 201
    resp = client.post("/api/runs", json={"structure": example_pdb, "seed": 2})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "in_flight_limit"


def test_explicit_idempotency_key_returns_same_run(client, example_pdb):
    # Dedupe is opt-in per request via an explicit key (guards against double-
    # charging on retries); identical inputs without a key run fresh by default.
    hdr = {"Idempotency-Key": "k1"}
    a = client.post("/api/runs", json={"structure": example_pdb, "seed": 1}, headers=hdr).json()
    b = client.post("/api/runs", json={"structure": example_pdb, "seed": 1}, headers=hdr).json()
    assert a["id"] == b["id"]


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/does-not-exist").status_code == 404


def test_uncertain_submission_surfaced(client, example_pdb):
    resp = client.post("/api/runs", json={"structure": example_pdb, "seed": -2})
    assert resp.status_code == 201
    assert resp.json()["submission_uncertain"] is True


# -- auth ---------------------------------------------------------------------


@pytest.fixture
def auth_client(tmp_path) -> Iterator[TestClient]:
    settings = Settings(db_path=tmp_path / "auth.db", backend="fake", operator_token="s3cret")
    with TestClient(create_app(settings)) as c:
        yield c


def test_api_requires_token_when_configured(auth_client, example_pdb):
    assert auth_client.post("/api/runs", json={"structure": example_pdb}).status_code == 401


def test_api_accepts_valid_token(auth_client, example_pdb):
    resp = auth_client.post(
        "/api/runs",
        json={"structure": example_pdb, "seed": 1},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert resp.status_code == 201
