"""structure_3d runs expose an in-browser 3D viewer + a downloadable PDB."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

SEQ = "GGCACUUCGGUGCC"


@pytest.fixture
def rhofold_client(tmp_path) -> Iterator[TestClient]:
    settings = Settings(db_path=tmp_path / "t.db", backend="fake", method="rhofold")
    with TestClient(create_app(settings)) as c:
        yield c


def _succeed(c: TestClient) -> str:
    rid = c.post("/api/runs", json={"method": "rhofold", "sequence": SEQ}).json()["id"]
    for _ in range(6):
        if c.get(f"/api/runs/{rid}").json()["state"] in ("succeeded", "failed"):
            break
    return rid


def test_pdb_download(rhofold_client):
    rid = _succeed(rhofold_client)
    r = rhofold_client.get(f"/api/runs/{rid}/structure.pdb")
    assert r.status_code == 200
    assert "chemical/x-pdb" in r.headers["content-type"]
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment") and rid[:8] in cd and cd.rstrip().endswith('.pdb"')
    assert r.text.lstrip().startswith(("ATOM", "HETATM", "MODEL", "HEADER"))


def test_pdb_download_404_for_unknown(rhofold_client):
    assert rhofold_client.get("/api/runs/nope/structure.pdb").status_code == 404


def test_run_page_has_viewer_and_download(rhofold_client):
    rid = _succeed(rhofold_client)
    page = rhofold_client.get(f"/runs/{rid}").text
    assert 'id="mol3d"' in page            # viewer container
    assert 'id="pdbdata"' in page          # embedded PDB for the viewer
    assert "3Dmol" in page                 # viewer library
    assert f"/api/runs/{rid}/structure.pdb" in page  # download link
