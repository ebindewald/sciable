"""RhoFold+ comes up as a third method through the same framework.

Only a manifest + the new ``structure_3d`` output kind were added (plus the
worker runner, deferred). Selecting it is a config flip (SCIABLE_METHOD=rhofold).
These exercise the full lifecycle — catalog, form, generic submission, status,
3D-structure (PDB + pLDDT) output — against the fake backend, with no GPU.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

EXAMPLE_SEQ = "GGCACUUCGGUGCC"


@pytest.fixture
def rhofold_client(tmp_path) -> Iterator[TestClient]:
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


def test_catalog_and_method_page(rhofold_client):
    catalog = rhofold_client.get("/").text
    assert "RhoFold" in catalog and "gRNAde" in catalog  # both reachable
    page = rhofold_client.get("/methods/rhofold").text
    assert "RhoFold" in page
    assert "sequence" in page.lower()
    assert "Submit run" in page


def test_submit_sequence_returns_pdb(rhofold_client):
    resp = rhofold_client.post(
        "/api/runs", json={"method": "rhofold", "sequence": EXAMPLE_SEQ, "seed": 1}
    )
    assert resp.status_code == 201
    assert resp.json()["method"] == "rhofold"
    final = _poll(rhofold_client, resp.json()["id"])
    assert final["state"] == "succeeded"
    assert final["sequence"] == EXAMPLE_SEQ

    pdb = final["pdb"]
    assert pdb.startswith("ATOM") and "END" in pdb
    # one P atom per nucleotide in the placeholder structure
    assert pdb.count("ATOM") == len(EXAMPLE_SEQ)

    metrics = final["metrics"]
    assert metrics["length"] == len(EXAMPLE_SEQ)
    assert metrics["atoms"] == len(EXAMPLE_SEQ)
    # pLDDT parsed from the B-factor column lands in the generator's 40-95 band
    assert 40.0 <= metrics["plddt_min"] <= metrics["plddt_max"] <= 95.0
    assert metrics["plddt_min"] <= metrics["plddt_mean"] <= metrics["plddt_max"]
    # per-residue pLDDT list is surfaced and one value per residue
    assert len(final["plddt"]) == len(EXAMPLE_SEQ)


def test_bad_alphabet_is_422(rhofold_client):
    resp = rhofold_client.post("/api/runs", json={"method": "rhofold", "sequence": "GGGZZZCCC"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_alphabet"


def test_too_long_is_rejected(rhofold_client):
    resp = rhofold_client.post("/api/runs", json={"method": "rhofold", "sequence": "A" * 201})
    assert resp.status_code == 422


def test_use_example_returns_pdb(rhofold_client):
    resp = rhofold_client.post("/api/runs", json={"method": "rhofold", "use_example": True})
    assert resp.status_code == 201
    final = _poll(rhofold_client, resp.json()["id"])
    assert final["state"] == "succeeded"
    assert final["sequence"] == EXAMPLE_SEQ
    assert final["pdb"].startswith("ATOM")


def test_run_page_shows_structure(rhofold_client):
    run_id = rhofold_client.post(
        "/api/runs", json={"method": "rhofold", "sequence": EXAMPLE_SEQ, "seed": 1}
    ).json()["id"]
    rhofold_client.get(f"/runs/{run_id}")
    page = rhofold_client.get(f"/runs/{run_id}").text
    assert "SUCCEEDED" in page
    assert "Predicted 3D structure" in page
    assert "pLDDT" in page
    # fake backend output has no real engine, so the placeholder banner shows
    assert "Mockup output" in page


def test_plddt_parser_dedupes_per_residue():
    from app.api import plddt_from_pdb

    # two atoms for residue 1 (same pLDDT), one for residue 2 -> two values
    pdb = (
        "ATOM      1  P     A A   1       0.000   0.000   0.000  1.00 55.50           P\n"
        "ATOM      2  C1'   A A   1       1.000   0.000   0.000  1.00 55.50           C\n"
        "ATOM      3  P     U A   2       5.900   0.000   0.000  1.00 72.25           P\n"
        "TER\nEND\n"
    )
    assert plddt_from_pdb(pdb) == [55.5, 72.25]
