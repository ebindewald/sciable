"""Web UI (gRNAde active): method page renders, browser submit redirects, results show."""

from __future__ import annotations

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
    assert "Mockup" in resp.text  # provisional methods are clearly flagged


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
    run_id = client.post("/api/runs", json={"structure": example_pdb, "seed": 1}).json()["id"]
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
    resp = client.post("/api/runs", data={"use_example": "1", "design_count": "4", "seed": "1"})
    assert resp.status_code == 201
    assert resp.json()["inputs"]["design_count"] == 4


def test_browser_submit_redirects_to_run_page(client, example_pdb):
    resp = client.post(
        "/api/runs",
        headers=HTML,
        files={"structure_file": ("example.pdb", example_pdb, "text/plain")},
        data={"seed": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/runs/")


def test_run_page_progresses_to_results(client, example_pdb):
    created = client.post("/api/runs", json={"structure": example_pdb, "seed": 1}).json()
    run_id = created["id"]
    client.get(f"/runs/{run_id}")
    page = client.get(f"/runs/{run_id}").text
    assert "SUCCEEDED" in page
    assert "design_1" in page  # Jinja escapes the leading '>' to '&gt;'


def test_run_page_404(client):
    assert client.get("/runs/nope").status_code == 404
