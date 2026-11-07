"""Idempotency + in-flight cap: identical submissions never launch a second job."""

from __future__ import annotations

import pytest

from app.backends.fake import FakeBackend
from app.bootstrap import ensure_seeded
from app.config import Settings
from app.db import init_db
from app.runs import RunConflictError, RunService


def _raw(example_pdb, **kw):
    return {"structure": example_pdb, **kw}


def test_identical_submission_dedupes_when_enabled(tmp_path, manifest, example_pdb):
    # The input-fingerprint cache is opt-in via dedupe_identical_inputs.
    settings = Settings(
        db_path=tmp_path / "dedupe.db", backend="fake", dedupe_identical_inputs=True
    )
    init_db(settings.db_path)
    deployment_id = ensure_seeded(settings, manifest)
    service = RunService(settings, manifest, FakeBackend())
    a = service.create_run(deployment_id=deployment_id, raw=_raw(example_pdb, seed=5))
    b = service.create_run(deployment_id=deployment_id, raw=_raw(example_pdb, seed=5))
    assert a.id == b.id
    assert a.provider_job_id == b.provider_job_id


def test_identical_submission_runs_fresh_by_default(run_service, example_pdb):
    # Caching is off by default: an identical resubmission (after the prior run is
    # terminal) launches a NEW run — the common development loop of re-running the
    # same input. An explicit idempotency key is the opt-in for dedupe.
    service, deployment_id = run_service
    a = service.create_run(deployment_id=deployment_id, raw=_raw(example_pdb, seed=5))
    service.poll_run(a.id)
    service.poll_run(a.id)  # drive the fake backend run to a terminal state
    b = service.create_run(deployment_id=deployment_id, raw=_raw(example_pdb, seed=5))
    assert a.id != b.id
    assert a.input_fingerprint == b.input_fingerprint  # same inputs, distinct runs


def test_explicit_idempotency_key_dedupes(run_service, example_pdb):
    service, deployment_id = run_service
    a = service.create_run(
        deployment_id=deployment_id, raw=_raw(example_pdb, seed=5), idempotency_key="k1"
    )
    b = service.create_run(
        deployment_id=deployment_id, raw=_raw(example_pdb, seed=6), idempotency_key="k1"
    )
    assert a.id == b.id


def test_different_inputs_make_distinct_runs(run_service, example_pdb):
    service, deployment_id = run_service
    a = service.create_run(deployment_id=deployment_id, raw=_raw(example_pdb, seed=5))
    service.poll_run(a.id)
    service.poll_run(a.id)
    b = service.create_run(deployment_id=deployment_id, raw=_raw(example_pdb, seed=6))
    assert a.id != b.id
    assert a.input_fingerprint != b.input_fingerprint


def test_in_flight_cap_enforced(run_service, example_pdb):
    service, deployment_id = run_service
    service.create_run(deployment_id=deployment_id, raw=_raw(example_pdb, seed=1))
    with pytest.raises(RunConflictError):
        service.create_run(deployment_id=deployment_id, raw=_raw(example_pdb, seed=2))
