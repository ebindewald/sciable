"""Web UI (gRNAde active): method page renders, browser submit redirects, results show."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

HTML = {"Accept": "text/html"}


def test_landing_lists_both_methods(client):
    text = client.get("/").text
    assert "gRNAde" in text and "structRFM" in text
    assert 'href="/methods/grnade"' in text
    assert 'href="/methods/structrfm"' in text


def test_method_page_renders(client):
    resp = client.get("/methods/grnade")
    assert resp.status_code == 200
    assert "gRNAde" in resp.text
    assert "Submit run" in resp.text
    assert 'name="method" value="grnade"' in resp.text  # hidden method field
    # gRNAde now has a real (checkpoint-gated) runner → provisional, not MOCKUP.
    assert "provisional (pre-baseline)" in resp.text
    assert "MOCKUP" not in resp.text


def test_unknown_method_page_404(client):
    assert client.get("/methods/nope").status_code == 404


def test_method_page_shows_example_choice(client):
    text = client.get("/methods/grnade").text
    assert "Use the bundled example" in text
    assert "chain A" in text                           # example details inline
    assert 'href="/methods/grnade/example"' in text    # link to view the input


def test_method_page_shows_example_description_and_info(client):
    text = client.get("/methods/grnade").text
    assert "synthetic 6-nucleotide" in text
    assert "More about this example" in text
    assert "inverse folding" in text


def test_run_page_shows_metrics_and_raw_output(client, example_pdb):
    run_id = client.post(
        "/api/runs", json={"method": "grnade", "structure": example_pdb, "seed": 1}
    ).json()["id"]
    client.get(f"/runs/{run_id}")
    page = client.get(f"/runs/{run_id}").text
    assert "Candidate designs" in page
    assert "GC%" in page
    assert "Raw program output" in page
    assert f"/api/runs/{run_id}/raw" in page


def test_example_route_serves_pdb(client):
    resp = client.get("/methods/grnade/example")
    assert resp.status_code == 200
    assert "ATOM" in resp.text


def test_submit_via_example_radio(client):
    resp = client.post(
        "/api/runs", data={"method": "grnade", "use_example": "1", "design_count": "4", "seed": "1"}
    )
    assert resp.status_code == 201
    assert resp.json()["inputs"]["design_count"] == 4


def test_browser_submit_redirects_to_run_page(client, example_pdb):
    resp = client.post(
        "/api/runs",
        headers=HTML,
        files={"structure_file": ("example.pdb", example_pdb, "text/plain")},
        data={"method": "grnade", "seed": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/runs/")


def test_browser_submit_error_renders_html_not_json(client, example_pdb):
    # Fill the single in-flight slot, then resubmit: the in_flight_limit (409) must
    # come back as an HTML page for a browser, never a raw JSON body.
    client.post("/api/runs", json={"method": "grnade", "structure": example_pdb, "seed": 1})
    resp = client.post(
        "/api/runs",
        headers=HTML,
        files={"structure_file": ("example.pdb", example_pdb, "text/plain")},
        data={"method": "grnade", "seed": "2"},
    )
    assert resp.status_code == 409
    assert resp.headers["content-type"].startswith("text/html")
    assert "in progress" in resp.text  # the error template's headline
    assert '"detail"' not in resp.text  # not a JSON error body


def test_api_submit_error_stays_json(client, example_pdb):
    # The same conflict for an API client (no text/html) stays machine-readable.
    client.post("/api/runs", json={"method": "grnade", "structure": example_pdb, "seed": 1})
    resp = client.post("/api/runs", json={"method": "grnade", "structure": example_pdb, "seed": 2})
    assert resp.status_code == 409
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["detail"]["code"] == "in_flight_limit"


def test_run_page_progresses_to_results(client, example_pdb):
    created = client.post(
        "/api/runs", json={"method": "grnade", "structure": example_pdb, "seed": 1}
    ).json()
    run_id = created["id"]
    client.get(f"/runs/{run_id}")
    page = client.get(f"/runs/{run_id}").text
    assert "SUCCEEDED" in page
    assert "design_1" in page  # Jinja escapes the leading '>' to '&gt;'


def test_run_page_shows_elapsed_indicator(client, example_pdb):
    created = client.post(
        "/api/runs", json={"method": "grnade", "structure": example_pdb, "seed": 1}
    ).json()
    page = client.get(f"/runs/{created['id']}").text
    assert 'id="elapsed"' in page
    assert f'data-start="{created["submitted_at"]}"' in page  # client ticker reads this


def test_run_page_404(client):
    assert client.get("/runs/nope").status_code == 404


def test_provisional_method_is_flagged(client):
    """gRNAde now has a real checkpoint-gated runner, so it is 'provisional'
    (real-but-unvalidated) rather than MOCKUP: the UI must show the provisional
    wording and never the MOCKUP badge, and a demo-backend (stub) result must
    still carry the 'Mockup output' placeholder warning."""
    assert "provisional (pre-baseline)" in client.get("/").text         # landing card
    assert "provisional (pre-baseline)" in client.get("/methods/grnade").text
    assert "MOCKUP" not in client.get("/methods/grnade").text
    run = client.post(
        "/api/runs", json={"method": "grnade", "use_example": True}
    ).json()
    page = ""
    for _ in range(8):
        page = client.get(f"/runs/{run['id']}").text  # run_page polls/advances the run
        if client.get(f"/api/runs/{run['id']}").json()["state"] == "succeeded":
            page = client.get(f"/runs/{run['id']}").text
            break
    # Fake backend runs the stub, so the result is a flagged placeholder, not real design.
    assert "Mockup output" in page


def test_real_method_not_flagged_mockup(rhofold_client):
    """A real (non-mockup) method must NOT carry the MOCKUP badge."""
    assert "MOCKUP" not in rhofold_client.get("/methods/rhofold").text


@pytest.fixture
def rhofold_client(tmp_path):
    settings = Settings(db_path=tmp_path / "r.db", backend="fake")
    with TestClient(create_app(settings)) as c:
        yield c
