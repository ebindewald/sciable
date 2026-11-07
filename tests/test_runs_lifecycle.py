"""Run lifecycle against the FakeBackend: happy path, failure, stuck, cancel."""

from __future__ import annotations

import json
from pathlib import Path

from app.models import RunState


def _drive(service, run, max_polls=5):
    for _ in range(max_polls):
        run = service.poll_run(run.id)
        if run.state in {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELED}:
            break
    return run


def _submit(service, deployment_id, example_pdb, seed):
    return service.create_run(
        deployment_id=deployment_id, raw={"structure": example_pdb, "seed": seed}
    )


def test_happy_path_succeeds_and_writes_outputs(run_service, example_pdb):
    service, deployment_id = run_service
    run = _submit(service, deployment_id, example_pdb, 1)
    assert run.state == RunState.QUEUED
    assert run.provider_job_id is not None

    run = service.poll_run(run.id)
    assert run.state == RunState.RUNNING
    run = service.poll_run(run.id)
    assert run.state == RunState.SUCCEEDED
    assert run.finished_at is not None

    outputs = json.loads(Path(run.outputs_ref).read_text())
    assert len(outputs["sequences"]) == run.inputs["design_count"]
    assert set(outputs["sequences"][0]) <= set("ACGU")


def test_failure_path(run_service, example_pdb):
    service, deployment_id = run_service
    run = _submit(service, deployment_id, example_pdb, -1)
    run = _drive(service, run)
    assert run.state == RunState.FAILED
    assert run.error_category == "worker_error"


def test_failure_detail_is_persisted_and_readable(run_service, example_pdb):
    """A failed run keeps the worker's detail (stderr tail), retrievable despite
    having no outputs_ref — so failures are debuggable in-app, not only in logs."""
    service, deployment_id = run_service
    run = _submit(service, deployment_id, example_pdb, -1)
    run = _drive(service, run)
    assert run.state == RunState.FAILED
    assert run.outputs_ref is None  # no outputs for a failed run
    raw = service.read_raw(run.id)
    assert raw is not None
    assert "simulated failure" in raw["detail"]


def test_uncertain_submission_not_resubmitted(run_service, example_pdb):
    service, deployment_id = run_service
    run = _submit(service, deployment_id, example_pdb, -2)
    assert run.state == RunState.QUEUED
    assert run.provider_job_id is None
    assert run.error_category == "submit_uncertain"
    assert service.poll_run(run.id).provider_job_id is None


def test_stuck_job_stays_running(run_service, example_pdb):
    service, deployment_id = run_service
    run = _submit(service, deployment_id, example_pdb, -3)
    run = _drive(service, run)
    assert run.state == RunState.RUNNING


def test_cancel_then_poll(run_service, example_pdb):
    service, deployment_id = run_service
    run = _submit(service, deployment_id, example_pdb, -3)
    assert service.backend.cancel(run.provider_job_id) is True
    run = service.poll_run(run.id)
    assert run.state == RunState.CANCELED
