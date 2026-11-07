"""The raw-output endpoint serves both an inline view and a file download."""

from __future__ import annotations


def _run_to_terminal(client, example_pdb):
    rid = client.post("/api/runs", json={"structure": example_pdb, "seed": 1}).json()["id"]
    for _ in range(6):
        if client.get(f"/api/runs/{rid}").json()["state"] in ("succeeded", "failed"):
            break
    return rid


def test_raw_inline_vs_download(client, example_pdb):
    rid = _run_to_terminal(client, example_pdb)

    inline = client.get(f"/api/runs/{rid}/raw")
    assert inline.status_code == 200
    assert "attachment" not in inline.headers.get("content-disposition", "")
    assert inline.json()["backend"] == "fake"

    dl = client.get(f"/api/runs/{rid}/raw?download=1")
    assert dl.status_code == 200
    cd = dl.headers["content-disposition"]
    assert cd.startswith("attachment")
    assert rid[:8] in cd
    assert dl.json()["backend"] == "fake"  # same content, just delivered as a file


def test_download_link_on_run_page(client, example_pdb):
    rid = _run_to_terminal(client, example_pdb)
    page = client.get(f"/runs/{rid}").text
    assert f"/api/runs/{rid}/raw?download=1" in page
