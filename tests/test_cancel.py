"""Run cancellation and auto-expiry of stale uncertain submissions."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.backends.runpod import RunpodBackend
from app.bootstrap import ensure_seeded
from app.config import Settings
from app.db import get_conn
from app.main import create_app
from app.models import RunState
from app.runs import RunConflictError, RunNotCancelableError, RunService

HTML = {"Accept": "text/html"}


def _submit(client, example_pdb, seed):
    resp = client.post("/api/runs", json={"structure": example_pdb, "seed": seed})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _backdate(db_path, run_id, seconds):
    ts = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    with get_conn(db_path) as conn:
        conn.execute("UPDATE run SET submitted_at = ? WHERE id = ?", (ts, run_id))


# -- API: cancel ----------------------------------------------------------------


def test_cancel_queued_run_frees_slot(client, example_pdb):
    run = _submit(client, example_pdb, 1)
    assert run["state"] == "queued"

    resp = client.post(f"/api/runs/{run['id']}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "canceled"
    assert body["finished_at"] is not None
    assert body["provider_cancel"] == "requested"

    # Stays canceled on re-poll, and a new submission is no longer blocked.
    assert client.get(f"/api/runs/{run['id']}").json()["state"] == "canceled"
    assert client.post("/api/runs", json={"structure": example_pdb, "seed": 2}).status_code == 201


def test_cancel_running_run(client, example_pdb):
    run = _submit(client, example_pdb, -3)  # stuck job
    assert client.get(f"/api/runs/{run['id']}").json()["state"] == "running"

    body = client.post(f"/api/runs/{run['id']}/cancel").json()
    assert body["state"] == "canceled"
    assert body["provider_cancel"] == "requested"


def test_cancel_uncertain_run_has_no_provider_call(client, example_pdb):
    run = _submit(client, example_pdb, -2)
    assert run["submission_uncertain"] is True

    body = client.post(f"/api/runs/{run['id']}/cancel").json()
    assert body["state"] == "canceled"
    assert body["provider_cancel"] == "not_applicable"
    assert body["submission_uncertain"] is False
    assert client.post("/api/runs", json={"structure": example_pdb, "seed": 2}).status_code == 201


def test_cancel_terminal_run_is_409(client, example_pdb):
    run = _submit(client, example_pdb, 1)
    for _ in range(3):
        client.get(f"/api/runs/{run['id']}")
    assert client.get(f"/api/runs/{run['id']}").json()["state"] == "succeeded"

    resp = client.post(f"/api/runs/{run['id']}/cancel")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "not_cancelable"


def test_cancel_unknown_run_is_404(client):
    assert client.post("/api/runs/nope/cancel").status_code == 404


@pytest.fixture
def auth_client(tmp_path) -> Iterator[TestClient]:
    settings = Settings(db_path=tmp_path / "auth.db", backend="fake", operator_token="s3cret")
    with TestClient(create_app(settings)) as c:
        yield c


def test_cancel_requires_operator(auth_client, example_pdb):
    auth = {"Authorization": "Bearer s3cret"}
    run_id = auth_client.post(
        "/api/runs", json={"structure": example_pdb, "seed": 1}, headers=auth
    ).json()["id"]
    assert auth_client.post(f"/api/runs/{run_id}/cancel").status_code == 401
    resp = auth_client.post(f"/api/runs/{run_id}/cancel", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["state"] == "canceled"


# -- web UI -----------------------------------------------------------------------


def test_run_page_shows_cancel_button_until_terminal(client, example_pdb):
    run = _submit(client, example_pdb, -3)
    page = client.get(f"/runs/{run['id']}").text
    assert f'action="/api/runs/{run["id"]}/cancel"' in page
    assert "Cancel run" in page

    resp = client.post(f"/api/runs/{run['id']}/cancel", headers=HTML, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/runs/{run['id']}"

    page = client.get(f"/runs/{run['id']}").text
    assert "CANCELED" in page
    assert "Cancel run" not in page


# -- service with a mocked Runpod backend -----------------------------------------


@pytest.fixture
def runpod_calls(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "testkey")
    return []


def _runpod_service(settings, manifest, calls, cancel_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/run"):
            return httpx.Response(200, json={"id": "rp-job-1", "status": "IN_QUEUE"})
        if "/cancel/" in request.url.path:
            return httpx.Response(cancel_status, json={"status": "CANCELLED"})
        return httpx.Response(200, json={"id": "rp-job-1", "status": "IN_PROGRESS"})

    rp_settings = settings.model_copy(update={"backend": "runpod", "runpod_endpoint_id": "ep123"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = RunService(rp_settings, manifest, RunpodBackend(rp_settings, client=client))
    # Seed the runpod-backend deployment: routing follows the DEPLOYMENT's
    # endpoint_ref (ep123 here), not a startup-bound global setting.
    return service, ensure_seeded(rp_settings, manifest)


def test_cancel_calls_runpod_cancel(run_service, settings, manifest, example_pdb, runpod_calls):
    service, deployment_id = _runpod_service(settings, manifest, runpod_calls)
    run = service.create_run(deployment_id=deployment_id, raw={"structure": example_pdb, "seed": 1})
    assert service.poll_run(run.id).state == RunState.RUNNING

    canceled, provider_canceled = service.cancel_run(run.id)
    assert canceled.state == RunState.CANCELED
    assert provider_canceled is True
    assert ("POST", "/v2/ep123/cancel/rp-job-1") in runpod_calls

    # Terminal now: no further provider status calls on poll, cancel is refused.
    n = len(runpod_calls)
    assert service.poll_run(run.id).state == RunState.CANCELED
    assert len(runpod_calls) == n
    with pytest.raises(RunNotCancelableError):
        service.cancel_run(run.id)


def test_runpod_cancel_failure_still_cancels_locally(
    run_service, settings, manifest, example_pdb, runpod_calls
):
    service, deployment_id = _runpod_service(settings, manifest, runpod_calls, cancel_status=500)
    run = service.create_run(deployment_id=deployment_id, raw={"structure": example_pdb, "seed": 1})

    canceled, provider_canceled = service.cancel_run(run.id)
    assert provider_canceled is False
    assert canceled.state == RunState.CANCELED


# -- auto-expiry of uncertain submissions ----------------------------------------


def test_stale_uncertain_run_expires_and_unblocks(run_service, settings, example_pdb):
    service, deployment_id = run_service
    stuck = service.create_run(
        deployment_id=deployment_id, raw={"structure": example_pdb, "seed": -2}
    )
    assert stuck.state == RunState.QUEUED and stuck.provider_job_id is None

    _backdate(settings.db_path, stuck.id, settings.uncertain_submit_expiry_s + 1)
    fresh = service.create_run(
        deployment_id=deployment_id, raw={"structure": example_pdb, "seed": 1}
    )
    assert fresh.state == RunState.QUEUED

    expired = service.get_run(stuck.id)
    assert expired.state == RunState.FAILED
    assert expired.error_category == "submit_uncertain"
    assert expired.finished_at is not None


def test_recent_uncertain_run_still_blocks(run_service, example_pdb):
    service, deployment_id = run_service
    service.create_run(deployment_id=deployment_id, raw={"structure": example_pdb, "seed": -2})
    with pytest.raises(RunConflictError):
        service.create_run(deployment_id=deployment_id, raw={"structure": example_pdb, "seed": 1})


def test_stale_uncertain_run_expires_on_read(client, settings, example_pdb):
    run = _submit(client, example_pdb, -2)
    _backdate(settings.db_path, run["id"], settings.uncertain_submit_expiry_s + 1)
    body = client.get(f"/api/runs/{run['id']}").json()
    assert body["state"] == "failed"
    assert body["error_category"] == "submit_uncertain"
    assert body["submission_uncertain"] is False
